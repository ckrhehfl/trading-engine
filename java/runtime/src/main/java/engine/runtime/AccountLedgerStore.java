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
 * stop. Callers needing a consistent read-modify-write cycle across
 * multiple processes must hold {@link AccountLedgerLock} around the whole
 * {@code load} + mutate + {@code persist} sequence themselves -- this
 * class provides no locking of its own.
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
     */
    static AccountLedger load(Path ledgerPath, String venue, String accountId, BigDecimal defaultAllocatedCapital) {
        Objects.requireNonNull(ledgerPath, "ledgerPath is required");
        Objects.requireNonNull(venue, "venue is required");
        Objects.requireNonNull(accountId, "accountId is required");
        Objects.requireNonNull(defaultAllocatedCapital, "defaultAllocatedCapital is required");

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
                            + " confirm no real ledger ever existed for this venue/account) before proceeding.");
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
        try {
            Path parent = ledgerPath.toAbsolutePath().getParent();
            if (parent != null) {
                Files.createDirectories(parent);
            }
            Path tmp = tmpPathFor(ledgerPath);
            byte[] content = MAPPER.writeValueAsBytes(ledger);
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
                Files.move(tmp, ledgerPath, StandardCopyOption.REPLACE_EXISTING);
            }
        } catch (IOException e) {
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
