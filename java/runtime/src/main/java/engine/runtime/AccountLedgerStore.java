package engine.runtime;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import java.io.IOException;
import java.math.BigDecimal;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.FileAlreadyExistsException;
import java.nio.file.Files;
import java.nio.file.NoSuchFileException;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
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
     */
    private static final ObjectMapper MAPPER = new ObjectMapper()
            .registerModule(new JavaTimeModule())
            .disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS);

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
            if (Files.exists(tmp)) {
                throw new IllegalStateException(
                        "account ledger file " + ledgerPath + " is missing but a leftover " + tmp + " exists --"
                                + " this strongly suggests a persist() was interrupted mid-replace (most likely"
                                + " the non-atomic REPLACE_EXISTING fallback -- see persist()'s own Javadoc),"
                                + " which could mean a real, previously-persisted ledger (including other"
                                + " processes' committed reservations) was lost. Refusing to silently bootstrap a"
                                + " fresh, empty ledger in this ambiguous case -- a human must investigate and"
                                + " manually resolve (e.g. recover from the .tmp file's content if it's valid, or"
                                + " confirm no real ledger ever existed for this venue/account) before proceeding.");
            }
            return freshLedger(venue, accountId, defaultAllocatedCapital);
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
            Files.writeString(tmp, MAPPER.writeValueAsString(ledger));
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
