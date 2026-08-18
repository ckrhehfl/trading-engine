package engine.runtime;

import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import java.io.IOException;
import java.math.BigDecimal;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.FileAlreadyExistsException;
import java.nio.file.Files;
import java.nio.file.NoSuchFileException;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.nio.file.StandardOpenOption;
import java.nio.file.attribute.BasicFileAttributes;
import java.time.Instant;
import java.util.List;
import java.util.Objects;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Durable, single-file JSON read/write layer for the shared {@link
 * AccountLedger} -- see that record's own Javadoc and the governing plan's
 * "2. The shared ledger" section for the full design. Same temp-file +
 * {@code ATOMIC_MOVE} write pattern, and the same fail-closed-on-corrupt-
 * read behavior, as {@code SubmissionMarkerStore} -- that class is this
 * one's direct template (see {@link #load}/{@link #persist} Javadoc below
 * for exactly where the two diverge and why).
 *
 * <p><b>Deliberate deviation from {@code SubmissionMarkerStore}'s pattern:
 * no caching, ever, and no instance state at all.</b> {@code
 * SubmissionMarkerStore} loads once at construction and caches the result
 * in memory -- safe only because it assumes a single writer per process.
 * This class will be read and written by <i>multiple independent OS
 * processes</i> sharing the same file (once {@code SharedKisAccountLedger},
 * Task C, wires it in) -- a cached, stale-by-construction in-memory copy
 * would silently ignore a sibling process's own concurrent writes. Both
 * {@link #load} and {@link #persist} are {@code static} with no instance
 * state whatsoever, precisely to make that class of bug structurally
 * impossible to introduce later rather than merely a documented rule to
 * remember: every call re-reads (or re-writes) the file on disk, full
 * stop.
 *
 * <p><b>Caller contract: every {@link #load}/{@link #persist} call, not
 * only a read-modify-write cycle, must be made while holding {@link
 * AccountLedgerLock}</b> -- this class provides no locking of its own.
 * Structurally enforced, not only documented: both methods require an
 * {@link AccountLedgerLock} argument -- constructible only via {@link
 * AccountLedgerLock#acquire} -- and reject a {@code null} or already-
 * released one via {@link AccountLedgerLock#requireHeld()}. This does
 * <b>not</b> verify that the supplied lock's own path corresponds to
 * {@code ledgerPath} -- no convention linking the two exists yet in this
 * codebase (that mapping is {@code SharedKisAccountLedger}, Task C's, to
 * define) -- so it proves <i>some</i> lock is held, not necessarily the
 * <i>correct</i> one for this specific ledger. {@link #tmpPathFor}
 * returns a fixed path ({@code <ledgerPath>.json.tmp}), not one unique
 * per process or per call; two processes calling {@link #persist} on the
 * same {@code ledgerPath} without holding the (correct) lock can race on
 * that shared temp file -- one process's write/rename can interleave
 * with another's, risking a partial or wrong write landing at {@code
 * ledgerPath} and a real loss of another process's already-committed
 * reservations. See {@link #persist}'s own Javadoc for the full
 * mechanism.
 */
final class AccountLedgerStore {

    private static final Logger log = LoggerFactory.getLogger(AccountLedgerStore.class);

    /**
     * Jackson configuration for this class's {@link AccountLedger} JSON --
     * {@code JavaTimeModule} for {@code Instant} support (see this
     * module's {@code build.gradle.kts} for why that dependency is needed
     * here but not added for {@code SubmissionMarker}), ISO-8601 strings
     * rather than epoch-array timestamps (human-readable, and consistent
     * with {@code SubmissionMarker}'s own {@code Instant#toString()}
     * convention elsewhere in this module). {@link AccountLedgerLock} keeps its
     * own, separately-configured copy of an equivalent mapper for its own
     * lock-metadata JSON, rather than sharing this one -- matching this
     * module's established "each durable-store-adjacent class keeps its
     * own copy" convention (see this class's own {@link AtomicMover}
     * Javadoc for the same convention applied to that interface).
     *
     * <p>{@code FAIL_ON_TRAILING_TOKENS} is enabled -- disabled by default
     * in Jackson (confirmed against 2.18.9's own documented behavior),
     * which means {@code readValue} silently ignores anything after the
     * first complete JSON value. A corrupted ledger file holding a valid
     * {@code AccountLedger} object followed by trailing garbage (or a
     * second, different value) would otherwise parse "successfully,"
     * directly undermining this class's own fail-closed contract on
     * corrupt input.
     */
    private static final ObjectMapper MAPPER = new ObjectMapper()
            .registerModule(new JavaTimeModule())
            .disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS)
            .enable(DeserializationFeature.FAIL_ON_TRAILING_TOKENS);

    private AccountLedgerStore() {}

    /** Testability seam -- see {@link #persist(Path, AccountLedger, AtomicMover, AccountLedgerLock)}'s Javadoc. */
    @FunctionalInterface
    interface AtomicMover {
        void move(Path source, Path target) throws IOException;
    }

    /**
     * Loads the ledger at {@code ledgerPath}. If the file does not exist
     * yet, returns a <b>freshly-bootstrapped</b> {@link AccountLedger} --
     * {@code venue}/{@code accountId}/{@code defaultAllocatedCapital} as
     * given, zero reservations, no alarm, {@code lastReconciledAt} {@code
     * null} (see {@link AccountLedger}'s own Javadoc for why {@code null}
     * rather than "now"). This is the ordinary, expected steady state the
     * very first time any process consults the ledger for a given {@code
     * (venue, accountId)} pair -- not an error.
     *
     * <p><b>Fails closed on any other read or parse failure</b> -- mirrors
     * {@code SubmissionMarkerStore#load()}'s own exact distinction: only a
     * genuinely missing file ({@link NoSuchFileException}) is treated as
     * "nothing recorded yet." Any other {@link IOException} (including a
     * malformed-JSON parse failure, which Jackson surfaces as a {@code
     * JsonProcessingException}, itself an {@code IOException} subtype) or a
     * structurally-valid-but-semantically-invalid file (e.g. valid JSON
     * missing a field {@link AccountLedger}'s own compact constructor
     * requires -- Jackson wraps the resulting {@code
     * NullPointerException} in a {@code ValueInstantiationException}, also
     * an {@code IOException} subtype, so this is caught by the same
     * branch) throws {@link IllegalStateException} rather than silently
     * returning a fresh ledger. A silent fresh-ledger fallback here would
     * be far more dangerous than {@code SubmissionMarkerStore}'s own
     * analogous case: it would not just discard a stale marker, it would
     * make every existing {@link LedgerReservation} -- another process's
     * real, currently-committed exposure -- invisible, undermining the
     * entire reason this shared ledger exists.
     *
     * <p><b>Manual resolution procedure for a human hitting this exception
     * because an <i>existing</i> {@code ledgerPath} file cannot be parsed
     * or fails one of its own structural checks</b> -- a genuinely
     * different scenario from the missing-ledger-plus-leftover-{@code
     * .tmp} procedure below: there, {@code ledgerPath} itself does not
     * exist at all; here, it exists but this call cannot trust its
     * content. The same fail-closed reasoning now also gates {@link
     * #persist}'s own {@code
     * verifyIdentityConsistency} check on the write side, for the
     * identical reason. (1) move the unparseable file to a backup path
     * (never delete it outright -- it may still hold real, recoverable
     * evidence of this account's last-known committed exposure); (2)
     * determine from other sources -- this venue/account's own operational
     * logs, a sibling process's independent state, or a real exchange-side
     * position/balance query -- whether the committed reservations this
     * ledger was tracking can be reconstructed; (3) if they can, a human
     * constructs a new, valid {@link AccountLedger} reflecting that
     * reconstructed state and persists it deliberately; if they cannot be
     * confidently reconstructed, do not guess and do not let this method
     * (or {@code persist}) silently regenerate an empty ledger over an
     * unknown real exposure -- this is exactly the ambiguous case this
     * class refuses to resolve automatically, matching the missing-
     * ledger-plus-{@code .tmp} procedure's own "do not guess" principle.
     * Automatic recovery is deliberately out of scope for the same
     * reasoning already given below for that case.
     *
     * <p><b>Also fails closed if the loaded file's own {@code venue}/{@code
     * accountId} don't match the requested ones.</b> Nothing upstream of
     * this method validates that {@code ledgerPath} actually corresponds
     * to {@code (venue, accountId)}; a future path-resolution bug or file
     * mix-up in Task C's caller would otherwise silently load one
     * account's real, currently-committed exposure and virtual capital
     * and use it to gate a <i>different</i> account's orders -- exactly
     * backwards for a class whose entire purpose is bounding a shared
     * account's real risk.
     *
     * <p><b>Also fails closed if {@code ledgerPath} is missing but a
     * leftover {@code .tmp} sibling exists</b> -- a real, disclosed
     * <i>partial</i> mitigation, not a claim of a complete fix. {@link
     * #persist}'s non-atomic fallback path ({@link
     * AtomicMoveNotSupportedException}/{@link FileAlreadyExistsException})
     * is not a single atomic operation; a process or host crash during
     * that specific fallback's {@link Files#move} could plausibly leave
     * {@code ledgerPath} genuinely missing (the old file already replaced,
     * the new content not durably in place) while its {@code .tmp} source
     * still lingers. Without this check, that state is indistinguishable
     * from "nothing has ever been persisted for this venue/account" --
     * {@code load} would silently return a <b>fresh, empty</b> ledger,
     * discarding every other process's real, previously-committed
     * reservation. A stray {@code .tmp} file next to a missing {@code
     * ledgerPath} is strong circumstantial evidence that isn't what
     * happened, so this fails closed instead of guessing. <b>Deliberately
     * not attempted here</b>: automatically recovering the ledger from the
     * {@code .tmp} file's own content, or maintaining a full backup/
     * generation history across every persist -- either is real,
     * additional, undesigned scope appropriate for a dedicated follow-up
     * if this fallback path is ever actually exercised in practice, not
     * this task's own storage/locking-primitives scope. This also does
     * not, and cannot, cover every possible crash timing in the
     * non-atomic fallback (e.g. one where the {@code .tmp} file itself is
     * also lost) -- named honestly as a partial mitigation for the same
     * reason.
     *
     * <p><b>Manual resolution procedure for a human hitting this
     * exception</b>: (1) read the {@code .tmp} file's own content and
     * confirm it parses as a well-formed {@link AccountLedger} (the same
     * validation this class applies on an ordinary {@code load} -- venue/
     * accountId match, no duplicate {@code clientOrderId}, no negative
     * {@code notional}, no half-populated reconciliation alarm pair); (2)
     * confirm its {@code venue}/{@code accountId} match what this ledger
     * is actually for; (3) if both check out, it is very likely the real,
     * intended content of the interrupted {@code persist()} -- rename it
     * to replace the (missing) {@code ledgerPath} directly, then retry
     * {@code load}; (4) if it doesn't parse, or the identity doesn't
     * match, do not guess -- this is exactly the ambiguous case this
     * method refuses to resolve automatically, and needs a human decision
     * informed by whatever else is known about the process history (e.g.
     * this venue/account's own last known real state from other
     * processes' logs) before either recovering or discarding it.
     * <b>Wiring an automated alert for this condition is deliberately not
     * done here</b> -- this project's own tooling roadmap defers
     * monitoring/alerting infrastructure to Priority #8 (24/7 unattended
     * operation) specifically because nothing runs unattended yet to
     * monitor, and this class is itself still standalone and unwired
     * (Task B) -- there is no running process yet for an alert to reach a
     * human through. The {@link IllegalStateException} message itself is
     * this task's own real, load-bearing signal in the meantime: it is
     * not swallowed, and propagates to whatever caller Task C eventually
     * wires in.
     *
     * <p><b>{@code defaultAllocatedCapital} must be strictly positive, and
     * also fails closed if an existing ledger's stored {@code
     * allocatedVirtualCapital} exceeds it</b> -- grounded directly in
     * CLAUDE.md's own "never weaken risk limits... without explicit human
     * approval" rule. {@code defaultAllocatedCapital} is otherwise only
     * ever consulted when bootstrapping a brand new ledger (see {@link
     * #freshLedger}) -- for an existing one, nothing previously compared
     * the stored value against the currently-configured default at all,
     * which would silently defeat an operator's own attempt to reduce a
     * risk budget: lowering {@code defaultAllocatedCapital} in
     * configuration would have no effect for as long as a larger,
     * previously-persisted value remains on disk. This method now throws
     * {@link IllegalArgumentException} for a non-positive {@code
     * defaultAllocatedCapital}, and {@link IllegalStateException} (the
     * same fail-closed treatment as the identity-mismatch case above,
     * <b>not</b> a silent auto-reduction) if a loaded ledger's {@code
     * allocatedVirtualCapital} is greater. Deliberately silent on whether
     * a <i>smaller</i> stored allocation should ever be raised back up to
     * a larger configured default -- that is real reconciliation policy
     * this class's own Javadoc already defers to {@code
     * SharedKisAccountLedger}/{@code AccountLedgerReconciler} (Task C/D),
     * not decided here.
     *
     * @param lock proof this caller currently holds {@link
     *     AccountLedgerLock} for this ledger -- see class Javadoc's
     *     "Caller contract" paragraph; rejected via {@link
     *     AccountLedgerLock#requireHeld()} if already released
     */
    static AccountLedger load(
            Path ledgerPath, String venue, String accountId, BigDecimal defaultAllocatedCapital, AccountLedgerLock lock) {
        Objects.requireNonNull(ledgerPath, "ledgerPath is required");
        Objects.requireNonNull(venue, "venue is required");
        Objects.requireNonNull(accountId, "accountId is required");
        Objects.requireNonNull(defaultAllocatedCapital, "defaultAllocatedCapital is required");
        Objects.requireNonNull(lock, "lock is required -- see class Javadoc's caller contract");
        lock.requireHeld();
        // A zero or negative allocated capital is meaningless for a risk
        // budget. Same "the record/method itself is the structural
        // enforcement point" reasoning already applied to
        // LedgerReservation#notional.
        if (defaultAllocatedCapital.signum() <= 0) {
            throw new IllegalArgumentException("defaultAllocatedCapital must be positive");
        }

        String raw;
        try {
            raw = Files.readString(ledgerPath);
        } catch (NoSuchFileException e) {
            Path tmp = tmpPathFor(ledgerPath);
            // Files.exists(...) swallows an I/O/access error into a plain
            // false -- indistinguishable from "genuinely absent," which
            // would defeat the whole point of this check by silently
            // bootstrapping fresh over an interrupted-replace state we
            // simply failed to positively rule out. Only a genuine
            // NoSuchFileException means "no .tmp"; any other failure fails
            // closed, matching this class's own established convention.
            try {
                Files.readAttributes(tmp, BasicFileAttributes.class);
            } catch (NoSuchFileException tmpAbsent) {
                return freshLedger(venue, accountId, defaultAllocatedCapital);
            } catch (IOException tmpCheckFailure) {
                // e (the NoSuchFileException that sent us down this branch in the
                // first place) is the only stack trace showing which path was
                // actually judged missing -- preserved as suppressed rather than
                // discarded (PMD's PreserveStackTrace rule). tmpCheckFailure
                // remains the primary cause; behavior is unchanged.
                tmpCheckFailure.addSuppressed(e);
                throw new IllegalStateException(
                        "failed to determine whether a leftover account ledger temp file " + tmp + " exists"
                                + " alongside missing " + ledgerPath + " -- refusing to silently bootstrap a fresh"
                                + " ledger without being able to positively rule out an interrupted persist()",
                        tmpCheckFailure);
            }
            throw new IllegalStateException(
                    "account ledger file " + ledgerPath + " is missing but a leftover " + tmp + " exists --"
                            + " this strongly suggests a persist() was interrupted mid-replace (most likely"
                            + " the non-atomic REPLACE_EXISTING fallback -- see persist()'s own Javadoc),"
                            + " which could mean a real, previously-persisted ledger (including other"
                            + " processes' committed reservations) was lost. Refusing to silently bootstrap a"
                            + " fresh, empty ledger in this ambiguous case -- a human must investigate and"
                            + " manually resolve (e.g. recover from the .tmp file's content if it's valid, or"
                            + " confirm no real ledger ever existed for this venue/account) before proceeding.",
                    e);
        } catch (IOException e) {
            throw new IllegalStateException(
                    "failed to read account ledger file " + ledgerPath + " -- refusing to start with unknown"
                            + " committed-exposure state rather than silently treating it as freshly bootstrapped",
                    e);
        }
        AccountLedger ledger = parseOrThrow(
                raw,
                "failed to parse account ledger file " + ledgerPath + " as an AccountLedger -- refusing to"
                        + " start with unknown committed-exposure state rather than silently treating it as"
                        + " freshly bootstrapped",
                "account ledger file " + ledgerPath + " parsed as the JSON literal null, not a real"
                        + " AccountLedger -- refusing to start with unknown committed-exposure state rather"
                        + " than silently treating it as freshly bootstrapped");
        if (!venue.equals(ledger.venue()) || !accountId.equals(ledger.accountId())) {
            throw new IllegalStateException(
                    "account ledger file " + ledgerPath + " holds venue/accountId (" + ledger.venue() + ", "
                            + ledger.accountId() + ") but (" + venue + ", " + accountId + ") was requested --"
                            + " refusing to use a mismatched ledger for a risk decision rather than silently"
                            + " proceeding with the wrong account's exposure");
        }
        // Fails closed, the same discipline already applied to the
        // identity mismatch just above -- see this method's own Javadoc
        // for the full reasoning (grounded in CLAUDE.md's "never weaken
        // risk limits" rule).
        if (ledger.allocatedVirtualCapital().compareTo(defaultAllocatedCapital) > 0) {
            throw new IllegalStateException(
                    "account ledger file " + ledgerPath + " holds allocatedVirtualCapital "
                            + ledger.allocatedVirtualCapital() + " but only " + defaultAllocatedCapital
                            + " is configured -- refusing to keep the larger persisted risk budget, which would"
                            + " silently ignore a configured reduction. A human must resolve this explicitly.");
        }
        return ledger;
    }

    private static AccountLedger freshLedger(String venue, String accountId, BigDecimal defaultAllocatedCapital) {
        return new AccountLedger(
                venue,
                accountId,
                defaultAllocatedCapital,
                BigDecimal.ZERO,
                BigDecimal.ZERO,
                BigDecimal.ZERO,
                null, // lastReconciledAt -- never reconciled yet, see class Javadoc
                null, // reconciliationAlarmTrippedAt -- no alarm
                null, // reconciliationAlarmReason
                List.of());
    }

    /** Convenience overload using the real {@code ATOMIC_MOVE} default -- see the 4-arg overload's Javadoc. */
    static void persist(Path ledgerPath, AccountLedger ledger, AccountLedgerLock lock) {
        persist(ledgerPath, ledger, AccountLedgerStore::defaultAtomicMove, lock);
    }

    /**
     * Persists {@code ledger} to {@code ledgerPath} via the same temp-file
     * + {@code ATOMIC_MOVE} (with a non-atomic-replace fallback for {@link
     * AtomicMoveNotSupportedException}/{@link FileAlreadyExistsException})
     * pattern {@code SubmissionMarkerStore#persist()} and {@code
     * DailyReportGenerator} already established in this codebase -- so a
     * process crash mid-write can never leave a half-written, corrupt file
     * behind for the next {@link #load} to (correctly) fail closed on.
     *
     * <p>{@code mover} mirrors {@code SubmissionMarkerStore}'s own {@code
     * AtomicMover} testability seam -- this codebase's established,
     * deliberate convention is that each durable-store class keeps its own
     * copy of this interface rather than sharing one across classes.
     *
     * <p><b>Caller contract: this method must only ever be called while
     * holding {@link AccountLedgerLock}, including a standalone call not
     * part of a {@code load} + mutate + {@code persist} cycle.</b>
     * Structurally enforced via the required {@code lock} parameter (see
     * class Javadoc's "Caller contract" paragraph for exactly what is,
     * and is not, verified about it). {@link #tmpPathFor} deliberately
     * keeps a fixed, non-process-unique temp path (the interrupted-
     * persist detection in {@link #load} depends on that fixed name to
     * recognize a leftover {@code .tmp} from a crashed prior attempt) --
     * but that same fixed path means two unsynchronized concurrent
     * {@code persist} calls against the same {@code ledgerPath} share one
     * temp file. One call's {@code TRUNCATE_EXISTING} open, write, and
     * rename can interleave with another's, risking a partial or
     * otherwise wrong write landing at {@code ledgerPath} -- a real loss
     * of another process's already-committed reservations, not merely a
     * hypothetical.
     *
     * <p><b>Durability: partial, not complete.</b> The temp file's content
     * is written via a {@link java.nio.channels.FileChannel} and {@link
     * java.nio.channels.FileChannel#force(boolean) force}d before the
     * rename, rather than a plain {@link Files#writeString} -- empirically
     * confirmed on this repository's real drvfs mount before relying on it
     * (a standalone probe writing and forcing a real file under {@code
     * java/runtime/build/tmp/} succeeded without throwing). This gives the
     * tmp file's own bytes a real durability guarantee against a crash
     * that happens <i>after</i> {@code persist} returns but before the OS
     * would otherwise have flushed them on its own. Parent-directory fsync
     * (needed for the rename/replace operation's own metadata to survive
     * a crash, not just the file's content) is <b>not</b> performed --
     * {@code java.nio.file} has no portable way to open and fsync a
     * directory. Also not retrofitted onto {@code SubmissionMarkerStore}/
     * {@code DailyReportGenerator}/{@code FileSignalSource}, this
     * codebase's other durable stores, which still use the plain {@code
     * Files.writeString} pattern this method itself used until now -- a
     * real, disclosed inconsistency across those four stores.
     *
     * <p><b>Refuses to silently overwrite an existing, different-identity
     * ledger.</b> Unlike {@link #load}, this method takes no separate
     * "expected {@code venue}/{@code accountId}" parameter -- the only
     * signal available to it is {@code ledgerPath}'s own existing content,
     * if any. Before ever replacing an existing file, {@link
     * #verifyIdentityConsistency} confirms that content -- when it is a
     * regular file at all -- parses as an {@link AccountLedger} whose
     * {@code venue}/{@code accountId} match {@code ledger}'s own, throwing
     * {@link IllegalStateException} (and leaving the existing file
     * completely untouched) on any mismatch, parse failure, or other read
     * failure. This guards the same class of mistake {@link #load}'s own
     * identity check guards on the read side -- a future path-resolution
     * bug or file mix-up in Task C's caller silently destroying a
     * <i>different</i> account's real, currently-committed reservations --
     * closed here on the write side too, before Task C's first real caller
     * exists to make the mistake. A pre-existing occupant that is not a
     * regular file at all (e.g. a directory left at this path by mistake)
     * is deliberately <b>not</b> rejected by this check -- it was never a
     * valid ledger to begin with, so there is nothing real for this check
     * to protect, and this method's own write/move machinery below still
     * fails loudly on it rather than silently replacing it (see {@code
     * AccountLedgerStoreTest#persistPreservesItsTmpFileWhenTheNonAtomicFallbackMoveItselfFails}).
     *
     * @param lock proof this caller currently holds {@link
     *     AccountLedgerLock} for this ledger -- see class Javadoc's
     *     "Caller contract" paragraph; rejected via {@link
     *     AccountLedgerLock#requireHeld()} if already released
     */
    static void persist(Path ledgerPath, AccountLedger ledger, AtomicMover mover, AccountLedgerLock lock) {
        Objects.requireNonNull(ledgerPath, "ledgerPath is required");
        Objects.requireNonNull(ledger, "ledger is required");
        Objects.requireNonNull(mover, "mover is required");
        Objects.requireNonNull(lock, "lock is required -- see class Javadoc's caller contract");
        lock.requireHeld();
        verifyIdentityConsistency(ledgerPath, ledger);
        // Hoisted outside the try block so the outer catch below can clean
        // it up -- see that catch's own comment for why.
        Path tmp = null;
        try {
            Path parent = ledgerPath.toAbsolutePath().getParent();
            if (parent != null) {
                Files.createDirectories(parent);
            }
            // tmp is assigned only to a file this call itself creates --
            // never to a pre-existing file, whether a genuine crash
            // leftover from an earlier persist() attempt (the exact
            // evidence load()'s own missing-ledger-plus-leftover-.tmp
            // fail-closed check depends on) or one this call simply
            // couldn't positively rule out. Serialization
            // (MAPPER.writeValueAsBytes) happens before tmp is ever
            // assigned, so a serialization failure never triggers cleanup
            // of a file this call didn't touch. Ownership of candidateTmp
            // is then decided before FileChannel.open is called at all
            // (not after), so an open() failure is covered by the same
            // ownership decision as a later write/force failure.
            byte[] content = MAPPER.writeValueAsBytes(ledger);
            Path candidateTmp = tmpPathFor(ledgerPath);
            // Only a genuine NoSuchFileException from
            // Files.readAttributes means "candidateTmp is absent" --
            // matching load()'s own convention above, and deliberately
            // not Files.exists, which swallows an I/O/access error into a
            // plain false indistinguishable from "genuinely absent."
            // Unlike load() (which fails the whole call closed on a
            // determination failure, since it has nothing useful to fall
            // back to), any other determination failure here treats
            // candidateTmp as pre-existing instead: tmp stays unassigned,
            // so it is never a cleanup candidate, but this persist()
            // attempt itself is not aborted -- the subsequent
            // FileChannel.open below may still succeed or fail
            // independently on its own terms.
            boolean tmpPreexisted;
            try {
                Files.readAttributes(candidateTmp, BasicFileAttributes.class);
                tmpPreexisted = true;
            } catch (NoSuchFileException absent) {
                tmpPreexisted = false;
            } catch (IOException determinationFailure) {
                tmpPreexisted = true;
            }
            if (!tmpPreexisted) {
                tmp = candidateTmp;
            } else {
                // A pre-existing candidateTmp AND a missing ledgerPath is
                // exactly the state load()'s own missing-ledger-plus-
                // leftover-.tmp fail-closed check treats as evidence of an
                // interrupted persist() requiring human resolution -- see
                // that check's own Javadoc for the manual resolution
                // procedure. Without this check, this call's own ordinary
                // CREATE + TRUNCATE_EXISTING write below would silently
                // consume that evidence. A confirmed-absent ledgerPath
                // (NoSuchFileException) triggers this check; a
                // confirmed-present ledgerPath proceeds to the write path
                // below (the ordinary retry case this method's own
                // leftover-tmp-overwrite behavior exists to support); a
                // ledgerPath whose existence cannot be determined
                // propagates to this method's outer catch, failing the
                // whole call closed. This duplicates verifyIdentityConsistency's
                // own earlier, equivalent check as a defensive redundancy
                // against a TOCTOU race between the two -- not dead code.
                try {
                    Files.readAttributes(ledgerPath, BasicFileAttributes.class);
                } catch (NoSuchFileException ledgerAbsent) {
                    throw new IllegalStateException(
                            "refusing to persist to missing " + ledgerPath + " while a leftover " + candidateTmp
                                    + " exists -- that temp file may be the only evidence of another process's"
                                    + " real, committed reservations. A human must resolve it first (see load()'s"
                                    + " own Javadoc for the manual resolution procedure).",
                            ledgerAbsent);
                }
            }
            // CREATE + TRUNCATE_EXISTING + WRITE, matching Files.writeString's
            // own documented default options exactly (not CREATE_NEW) --
            // a leftover tmp file from an earlier interrupted persist()
            // must still be overwritable on retry, the same as it always
            // was; CREATE_NEW would instead throw
            // FileAlreadyExistsException and break that retry.
            try (FileChannel channel = FileChannel.open(
                    candidateTmp,
                    StandardOpenOption.CREATE,
                    StandardOpenOption.TRUNCATE_EXISTING,
                    StandardOpenOption.WRITE)) {
                ByteBuffer buffer = ByteBuffer.wrap(content);
                while (buffer.hasRemaining()) {
                    channel.write(buffer);
                }
                channel.force(true);
            }
            try {
                mover.move(candidateTmp, ledgerPath);
            } catch (AtomicMoveNotSupportedException | FileAlreadyExistsException e) {
                // Same fallback SubmissionMarkerStore/DailyReportGenerator
                // already established for the identical class of failure
                // (some filesystems/cross-volume setups don't support
                // ATOMIC_MOVE at all, or implementation-specifically reject
                // an existing target rather than replace it) -- not
                // atomic, but strictly better than a permanent failure
                // loop.
                log.warn(
                        "atomic move not usable for {} -> {}, falling back to a non-atomic replace: {}",
                        candidateTmp,
                        ledgerPath,
                        e.toString());
                try {
                    Files.move(candidateTmp, ledgerPath, StandardCopyOption.REPLACE_EXISTING);
                } catch (IOException fallbackFailure) {
                    // This non-atomic REPLACE_EXISTING move is not a single
                    // atomic operation, and can fail partway through --
                    // possibly after it has already removed the old, real
                    // ledgerPath (which may hold other processes' genuine
                    // committed reservations) but before tmp's own content
                    // has actually landed at that path. In that specific
                    // sequence, tmp is the *only* remaining copy of valid
                    // data, and the outer catch's own cleanup (see its
                    // comment) would delete the one piece of evidence
                    // load()'s own missing-ledger-plus-leftover-.tmp
                    // fail-closed check needs to avoid silently
                    // bootstrapping an empty ledger over real, lost
                    // reservations -- turning a safe fail-closed outcome
                    // into silent data loss, exactly backwards. Setting
                    // tmp to null here (a plain local variable
                    // reassignment, not a lambda capture, so this is valid
                    // Java) opts this specific failure out of that
                    // cleanup -- tmp survives on disk, matching this same
                    // class's existing "a stray .tmp is treated as strong
                    // circumstantial evidence of an interrupted persist()"
                    // convention. The narrower, originally-intended
                    // cleanup case (mover.move itself failing, before any
                    // fallback and before ledgerPath is ever touched) is
                    // unaffected -- tmp there is genuinely this process's
                    // own unpublished, orphaned write, safe to remove.
                    //
                    // e (the AtomicMoveNotSupportedException/
                    // FileAlreadyExistsException that triggered entry into
                    // this fallback) is preserved as suppressed rather than
                    // lost (PMD's PreserveStackTrace rule) -- this path
                    // already means ledgerPath may have been altered and a
                    // human must investigate directly, so e's full stack
                    // is real diagnostic value here. Behavior unchanged.
                    // This unconditional reassignment composes correctly
                    // with tmpPreexisted above regardless of which value
                    // tmp already held: null if candidateTmp pre-existed
                    // (already excluded from cleanup), or the real,
                    // freshly-created path otherwise -- either way, this
                    // failure now leaves tmp null, so candidateTmp is never
                    // touched by the outer cleanup below.
                    fallbackFailure.addSuppressed(e);
                    tmp = null;
                    throw fallbackFailure;
                }
            }
        } catch (IOException e) {
            // Without this cleanup, a tmp file this same persist() call
            // just created -- e.g. mover.move throwing something other than
            // AtomicMoveNotSupportedException/FileAlreadyExistsException --
            // was left behind on disk. For a ledger that had never
            // successfully persisted before (ledgerPath still doesn't
            // exist), that leftover tmp then makes every future load() call
            // fail closed permanently via its own missing-ledger-plus-
            // leftover-.tmp check, indistinguishable from a genuinely
            // interrupted persist() -- until a human manually deletes the
            // file. Capital safety was never at risk (fail-closed is the
            // correct direction), but availability was needlessly lost for
            // a ledger that had simply never succeeded even once. Cleaned
            // up here, best-effort: a cleanup failure is attached via
            // addSuppressed rather than masking the original failure.
            // Deliberately does NOT change load()'s own detection logic or
            // weaken it -- a tmp file surviving a real crash (no Java
            // exception ever thrown to reach this catch block at all) is
            // untouched by this cleanup and still correctly fails closed.
            // Also deliberately does NOT run when tmp was set to null
            // above -- either because a fallback-move failure explicitly
            // cleared it (see that catch block's own comment), or because
            // tmpPreexisted was true and tmp was therefore never assigned
            // in the first place: a leftover from an earlier, different
            // persist() attempt (crashed, or itself failed with an
            // IOException) is never this call's own to clean up, no
            // matter which step of this call then fails.
            if (tmp != null) {
                try {
                    Files.deleteIfExists(tmp);
                } catch (IOException cleanupFailure) {
                    e.addSuppressed(cleanupFailure);
                }
            }
            // Deliberately propagates, matching SubmissionMarkerStore's own
            // write-failure convention -- a failed write means the
            // caller's just-persisted ledger state may not actually be
            // durable, which a caller holding AccountLedgerLock across a
            // read-modify-write cycle must know about, not silently
            // proceed past believing its mutation was recorded.
            throw new IllegalStateException("failed to persist account ledger file " + ledgerPath, e);
        }
    }

    /**
     * See {@link #persist(Path, AccountLedger, AtomicMover, AccountLedgerLock)}'s own Javadoc
     * for the full reasoning. Uses {@link Files#readAttributes} (not
     * {@link Files#exists}/{@link Files#isRegularFile}, both of which
     * swallow an I/O or permission error into a plain {@code false},
     * indistinguishable from "genuinely absent" -- the exact class of bug
     * fixed elsewhere in this same class) to positively distinguish
     * "genuinely absent" ({@link NoSuchFileException}, nothing to check),
     * "exists but is not a regular file" (skip -- never a valid ledger to
     * begin with, see {@link #persist}'s own Javadoc), and "exists as a
     * regular file" (must parse as a matching-identity {@link
     * AccountLedger}). Any other determination, read, or parse failure
     * fails closed by throwing -- unlike the {@code tmpPreexisted} check
     * in {@link #persist} itself (above), which deliberately fails toward
     * "treat as pre-existing, don't delete" on an undetermined state,
     * this check's own risk is the opposite direction (silently
     * overwriting real data, not wrongly deleting it), so an undetermined
     * state here must abort the call rather than let it proceed.
     */
    private static void verifyIdentityConsistency(Path ledgerPath, AccountLedger ledger) {
        BasicFileAttributes attrs;
        try {
            attrs = Files.readAttributes(ledgerPath, BasicFileAttributes.class);
        } catch (NoSuchFileException absent) {
            return;
        } catch (IOException determinationFailure) {
            throw new IllegalStateException(
                    "refusing to persist over " + ledgerPath + " -- its existing state could not be determined,"
                            + " so this call cannot confirm it is not silently destroying a different account's"
                            + " real, committed reservations. A human must resolve the existing path first.",
                    determinationFailure);
        }
        if (!attrs.isRegularFile()) {
            return;
        }
        String existingRaw;
        try {
            existingRaw = Files.readString(ledgerPath);
        } catch (IOException readFailure) {
            throw new IllegalStateException(
                    "refusing to persist over " + ledgerPath + " -- its existing content could not be read, so"
                            + " this call cannot confirm it is not silently destroying a different account's"
                            + " real, committed reservations. A human must resolve the existing file first.",
                    readFailure);
        }
        AccountLedger existing = parseOrThrow(
                existingRaw,
                "refusing to persist over " + ledgerPath + " -- its existing content could not be parsed as"
                        + " an AccountLedger, so this call cannot confirm it is not silently destroying a"
                        + " different account's real, committed reservations. A human must resolve the"
                        + " existing file first.",
                "refusing to persist over " + ledgerPath + " -- its existing content parsed as the JSON"
                        + " literal null, not a real AccountLedger, so this call cannot confirm it is not"
                        + " silently destroying a different account's real, committed reservations. A human"
                        + " must resolve the existing file first.");
        if (!existing.venue().equals(ledger.venue()) || !existing.accountId().equals(ledger.accountId())) {
            throw new IllegalStateException(
                    "refusing to persist ledger for (" + ledger.venue() + ", " + ledger.accountId() + ") over "
                            + ledgerPath + ", which already holds a real, persisted ledger for ("
                            + existing.venue() + ", " + existing.accountId() + ") -- this would silently destroy"
                            + " that account's committed reservations");
        }
    }

    /**
     * Shared by {@link #load} and {@link #verifyIdentityConsistency}:
     * both parse already-read ledger file content into an {@link
     * AccountLedger} and must fail closed identically on a genuine parse
     * failure or on a valid-JSON-but-null literal (the JSON literal
     * {@code "null"} is valid JSON, so Jackson returns a plain Java
     * {@code null} here without throwing rather than an error condition
     * from its own perspective -- left unchecked, a caller's very next
     * field access would throw a raw {@link NullPointerException}
     * instead of this class's own intended {@link IllegalStateException}
     * fail-closed contract). Each caller supplies its own exact,
     * pre-existing message text, unchanged by this extraction, since
     * both messages are referenced by this class's own manual-
     * resolution-procedure documentation.
     */
    private static AccountLedger parseOrThrow(String raw, String parseFailureMessage, String nullLiteralMessage) {
        AccountLedger parsed;
        try {
            parsed = MAPPER.readValue(raw, AccountLedger.class);
        } catch (IOException e) {
            throw new IllegalStateException(parseFailureMessage, e);
        }
        if (parsed == null) {
            throw new IllegalStateException(nullLiteralMessage);
        }
        return parsed;
    }

    private static void defaultAtomicMove(Path source, Path target) throws IOException {
        Files.move(source, target, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
    }

    private static Path tmpPathFor(Path ledgerPath) {
        return ledgerPath.resolveSibling(ledgerPath.getFileName() + ".tmp");
    }
}
