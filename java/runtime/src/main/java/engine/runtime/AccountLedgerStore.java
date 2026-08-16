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
 * <p><b>Caller contract: every {@link #persist} call, not only a
 * read-modify-write cycle, must be made while holding {@link
 * AccountLedgerLock}</b> -- this class provides no locking of its own.
 * {@link #tmpPathFor} returns a fixed path ({@code
 * <ledgerPath>.json.tmp}), not one unique per process or per call; two
 * processes calling {@link #persist} on the same {@code ledgerPath}
 * without holding the lock (even a standalone {@code persist} call
 * outside any {@code load} + mutate sequence) can race on that shared
 * temp file -- one process's write/rename can interleave with another's,
 * risking a partial or wrong write landing at {@code ledgerPath} and a
 * real loss of another process's already-committed reservations. See
 * {@link #persist}'s own Javadoc for the full mechanism.
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
     * <p>{@code FAIL_ON_TRAILING_TOKENS} is enabled -- a real Major
     * finding, real CodeRabbit review of this PR: it's disabled by
     * default in Jackson (confirmed against 2.18.9's own documented
     * behavior, not assumed), which means {@code readValue} silently
     * ignores anything after the first complete JSON value -- a corrupted
     * ledger file holding a valid {@code AccountLedger} object followed by
     * trailing garbage (or a second, different value) would otherwise
     * parse "successfully," directly undermining this class's own
     * fail-closed contract on corrupt input.
     */
    private static final ObjectMapper MAPPER = new ObjectMapper()
            .registerModule(new JavaTimeModule())
            .disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS)
            .enable(DeserializationFeature.FAIL_ON_TRAILING_TOKENS);

    private AccountLedgerStore() {}

    /** Testability seam -- see {@link #persist(Path, AccountLedger, AtomicMover)}'s Javadoc. */
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
     * <p><b>Also fails closed if the loaded file's own {@code venue}/{@code
     * accountId} don't match the requested ones</b> -- a real Major finding
     * from this task's own real CodeRabbit review. Nothing upstream of this
     * method validates that {@code ledgerPath} actually corresponds to
     * {@code (venue, accountId)}; a future path-resolution bug or file mix-
     * up in Task C's caller would otherwise silently load one account's
     * real, currently-committed exposure and virtual capital and use it to
     * gate a <i>different</i> account's orders -- exactly backwards for a
     * class whose entire purpose is bounding a shared account's real risk.
     *
     * <p><b>Also fails closed if {@code ledgerPath} is missing but a
     * leftover {@code .tmp} sibling exists</b> -- a real Major finding
     * from this task's own real CodeRabbit review (labeled "Heavy lift" by
     * the reviewer itself; this is a real, disclosed <i>partial</i>
     * mitigation, not a claim of a complete fix -- see below). {@link
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
     * generation history across every persist -- either would be real,
     * additional, undesigned scope (the reviewer's own "Heavy lift" label)
     * appropriate for a dedicated follow-up if this fallback path is ever
     * actually exercised in practice, not something to improvise under
     * review pressure on a task whose own scope is the storage/locking
     * primitives, not a full recovery system. This also does not, and
     * cannot, cover every possible crash timing in the non-atomic
     * fallback (e.g. one where the {@code .tmp} file itself is also lost)
     * -- named honestly as a partial mitigation for the same reason.
     *
     * <p><b>{@code defaultAllocatedCapital} must be strictly positive, and
     * also fails closed if an existing ledger's stored {@code
     * allocatedVirtualCapital} exceeds it</b> -- a real Minor finding from
     * this task's own real CodeRabbit review, grounded directly in
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
     */
    static AccountLedger load(Path ledgerPath, String venue, String accountId, BigDecimal defaultAllocatedCapital) {
        Objects.requireNonNull(ledgerPath, "ledgerPath is required");
        Objects.requireNonNull(venue, "venue is required");
        Objects.requireNonNull(accountId, "accountId is required");
        Objects.requireNonNull(defaultAllocatedCapital, "defaultAllocatedCapital is required");
        // Real Minor finding, real CodeRabbit review of this PR: nothing
        // previously stopped a caller from seeding a fresh ledger with a
        // zero or negative allocated capital, which is meaningless for a
        // risk budget. Same "the record/method itself is the structural
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
            // false (a real Major finding, real CodeRabbit review of this
            // PR) -- indistinguishable from "genuinely absent," which
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
                // discarded, real Trivial finding, real CodeRabbit review of this
                // PR (PMD's PreserveStackTrace). tmpCheckFailure remains the
                // primary cause; behavior is unchanged.
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
        AccountLedger ledger;
        try {
            ledger = MAPPER.readValue(raw, AccountLedger.class);
        } catch (IOException e) {
            throw new IllegalStateException(
                    "failed to parse account ledger file " + ledgerPath + " as an AccountLedger -- refusing to"
                            + " start with unknown committed-exposure state rather than silently treating it as"
                            + " freshly bootstrapped",
                    e);
        }
        if (ledger == null) {
            // A real Major finding, real CodeRabbit review of this PR:
            // the JSON literal "null" is valid JSON, so Jackson returns a
            // plain Java null here without throwing (not an error
            // condition from its own perspective) -- left unchecked, the
            // very next line's ledger.venue() would throw a raw
            // NullPointerException instead of this class's own intended
            // IllegalStateException fail-closed contract.
            throw new IllegalStateException(
                    "account ledger file " + ledgerPath + " parsed as the JSON literal null, not a real"
                            + " AccountLedger -- refusing to start with unknown committed-exposure state rather"
                            + " than silently treating it as freshly bootstrapped");
        }
        if (!venue.equals(ledger.venue()) || !accountId.equals(ledger.accountId())) {
            throw new IllegalStateException(
                    "account ledger file " + ledgerPath + " holds venue/accountId (" + ledger.venue() + ", "
                            + ledger.accountId() + ") but (" + venue + ", " + accountId + ") was requested --"
                            + " refusing to use a mismatched ledger for a risk decision rather than silently"
                            + " proceeding with the wrong account's exposure");
        }
        // Real Minor finding, real CodeRabbit review of this PR, grounded
        // directly in CLAUDE.md's own "never weaken risk limits... without
        // explicit human approval" rule: defaultAllocatedCapital is
        // otherwise only ever consulted when bootstrapping a brand new
        // ledger (see freshLedger below) -- for an existing one, the
        // stored allocatedVirtualCapital was previously used as-is with no
        // comparison against the currently-configured default at all. That
        // silently defeats an operator's own attempt to reduce a risk
        // budget: lowering defaultAllocatedCapital in configuration would
        // have no effect for as long as a larger, previously-persisted
        // value remains on disk. Fails closed instead, the same discipline
        // already applied to the identity mismatch just above -- not an
        // auto-reduce (which would silently mutate stored state on this
        // process's own initiative) and not a silent pass-through
        // (which is the exact bug this closes); a human must resolve it
        // explicitly. This deliberately says nothing about whether a
        // *smaller* stored allocation should ever be raised back up to a
        // larger configured default -- that is real reconciliation policy
        // this class's own Javadoc already defers to Task C/D, untouched
        // here.
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

    /** Convenience overload using the real {@code ATOMIC_MOVE} default -- see the 3-arg overload's Javadoc. */
    static void persist(Path ledgerPath, AccountLedger ledger) {
        persist(ledgerPath, ledger, AccountLedgerStore::defaultAtomicMove);
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
     * part of a {@code load} + mutate + {@code persist} cycle.</b> {@link
     * #tmpPathFor} deliberately keeps a fixed, non-process-unique temp
     * path (the interrupted-persist detection in {@link #load} depends on
     * that fixed name to recognize a leftover {@code .tmp} from a crashed
     * prior attempt) -- but that same fixed path means two unsynchronized
     * concurrent {@code persist} calls against the same {@code
     * ledgerPath} share one temp file. One call's {@code
     * TRUNCATE_EXISTING} open, write, and rename can interleave with
     * another's, risking a partial or otherwise wrong write landing at
     * {@code ledgerPath} -- a real loss of another process's already-
     * committed reservations, not merely a hypothetical. This is stated
     * as an unconditional caller contract now, before {@code
     * SharedKisAccountLedger} (Task C) becomes the first real caller, so
     * that wiring has no ambiguity to resolve incorrectly.
     *
     * <p><b>Partial durability improvement, real Major finding ("Heavy
     * lift"), real CodeRabbit review of this PR -- disclosed as partial,
     * not claimed complete.</b> The temp file's content is now written via
     * a {@link java.nio.channels.FileChannel} and {@link
     * java.nio.channels.FileChannel#force(boolean) force}d before the
     * rename, rather than the original plain {@link Files#writeString} --
     * empirically confirmed on this repository's real drvfs mount before
     * relying on it (a standalone probe writing and forcing a real file
     * under {@code java/runtime/build/tmp/} succeeded without throwing).
     * This gives the tmp file's own bytes a real durability guarantee
     * against a crash that happens <i>after</i> {@code persist} returns
     * but before the OS would otherwise have flushed them on its own.
     * <b>Deliberately not attempted here</b>: fsyncing the parent
     * directory itself (needed for the rename/replace operation's own
     * metadata to survive a crash, not just the file's content) --
     * {@code java.nio.file} has no portable way to open and fsync a
     * directory, and doing so via platform-specific APIs is real,
     * additional, undesigned scope for a task whose brief is the storage/
     * locking primitives, not a full crash-consistency system (matching
     * the reviewer's own "Heavy lift" label, and the same scope boundary
     * already drawn for the missing-ledger-plus-stray-.tmp mitigation
     * above). This also does not retrofit the same durability guarantee
     * onto {@code SubmissionMarkerStore}/{@code DailyReportGenerator}/
     * {@code FileSignalSource} -- this codebase's other existing durable
     * stores, all of which use the identical plain {@code
     * Files.writeString} pattern this method itself used until now, and
     * none of which fsync either. Applying a stronger durability
     * guarantee to only the newest of four structurally-identical stores,
     * without a deliberate decision to also revisit the other three,
     * would be a real, disclosed inconsistency, not a complete fix --
     * named here rather than silently introduced.
     */
    static void persist(Path ledgerPath, AccountLedger ledger, AtomicMover mover) {
        Objects.requireNonNull(ledgerPath, "ledgerPath is required");
        Objects.requireNonNull(ledger, "ledger is required");
        Objects.requireNonNull(mover, "mover is required");
        // Hoisted outside the try block (rather than declared inside, as
        // originally written) so the outer catch below can clean it up --
        // see that catch's own comment for why.
        Path tmp = null;
        try {
            Path parent = ledgerPath.toAbsolutePath().getParent();
            if (parent != null) {
                Files.createDirectories(parent);
            }
            // Real Minor finding, a further real CodeRabbit review round
            // on this same PR: serialization (MAPPER.writeValueAsBytes)
            // must complete BEFORE tmp is assigned, not after. If tmp were
            // assigned first and writeValueAsBytes then threw (a real
            // JsonProcessingException, an IOException subtype, is a real
            // possibility, not hypothetical), the outer catch's own
            // cleanup (see its own comment) would see a non-null tmp and
            // delete whatever file exists at that path -- but this call
            // never created or opened it via FileChannel.open below. If a
            // genuine crash from a different, earlier persist() attempt
            // had left a real leftover .tmp at that exact path -- the
            // exact evidence load()'s own missing-ledger-plus-leftover-
            // .tmp fail-closed check depends on -- this serialization
            // failure would incorrectly delete it, the same class of
            // silent-data-loss risk round 19's own fix closed for the
            // fallback-move-failure case. Assigning tmp only after
            // serialization succeeds means the cleanup logic's scope is
            // always exactly "a file this call itself opened," never a
            // stray survivor from elsewhere.
            byte[] content = MAPPER.writeValueAsBytes(ledger);
            tmp = tmpPathFor(ledgerPath);
            // CREATE + TRUNCATE_EXISTING + WRITE, matching Files.writeString's
            // own documented default options exactly (not CREATE_NEW) --
            // a leftover tmp file from an earlier interrupted persist()
            // must still be overwritable on retry, the same as it always
            // was; CREATE_NEW would instead throw
            // FileAlreadyExistsException and break that retry.
            try (FileChannel channel = FileChannel.open(
                    tmp, StandardOpenOption.CREATE, StandardOpenOption.TRUNCATE_EXISTING, StandardOpenOption.WRITE)) {
                ByteBuffer buffer = ByteBuffer.wrap(content);
                while (buffer.hasRemaining()) {
                    channel.write(buffer);
                }
                channel.force(true);
            }
            try {
                mover.move(tmp, ledgerPath);
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
                        tmp,
                        ledgerPath,
                        e.toString());
                try {
                    Files.move(tmp, ledgerPath, StandardCopyOption.REPLACE_EXISTING);
                } catch (IOException fallbackFailure) {
                    // Real Major finding, a further real CodeRabbit review
                    // round on this PR, catching a real regression in the
                    // round-18 cleanup fix below: this non-atomic
                    // REPLACE_EXISTING move is not a single atomic
                    // operation, and can fail partway through -- possibly
                    // after it has already removed the old, real
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
                    // Real Minor finding, a further real CodeRabbit review
                    // round on this same PR (PMD's PreserveStackTrace):
                    // without this line, the original e (the
                    // AtomicMoveNotSupportedException/
                    // FileAlreadyExistsException that triggered entry into
                    // this fallback in the first place) was only ever
                    // logged as a plain e.toString() above -- its own
                    // stack trace was lost once fallbackFailure propagated
                    // alone. This path already means ledgerPath may have
                    // been altered and a human must investigate directly;
                    // preserving e's full stack (not just its message)
                    // alongside fallbackFailure's own is real diagnostic
                    // value for that investigation. Behavior unchanged.
                    fallbackFailure.addSuppressed(e);
                    tmp = null;
                    throw fallbackFailure;
                }
            }
        } catch (IOException e) {
            // Real Major finding, real CodeRabbit review of this PR: without
            // this cleanup, a tmp file this same persist() call just created
            // -- e.g. mover.move throwing something other than
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
            // above -- see that catch block's own comment for the real,
            // separate reason a fallback-move failure must preserve tmp
            // rather than clean it up.
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

    private static void defaultAtomicMove(Path source, Path target) throws IOException {
        Files.move(source, target, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
    }

    private static Path tmpPathFor(Path ledgerPath) {
        return ledgerPath.resolveSibling(ledgerPath.getFileName() + ".tmp");
    }
}
