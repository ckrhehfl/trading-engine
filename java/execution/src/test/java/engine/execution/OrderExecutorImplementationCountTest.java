package engine.execution;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.regex.Pattern;
import java.util.stream.Collectors;
import java.util.stream.Stream;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

/**
 * There are, and must only ever be, <strong>exactly two</strong> production
 * {@link OrderExecutor} implementations.
 *
 * <p><strong>This is enforced in code because prose already failed to enforce
 * it once.</strong> An earlier version of Paper Trading Task H added a third —
 * {@code engine.runtime.PersistentSubmissionOrderExecutor}, a decorator
 * wrapping another executor to add durable {@code SUBMISSION_UNKNOWN} marking.
 * It read as obviously reasonable, the rule was written down at the time, and
 * it took a real CodeRabbit review to notice it genuinely violated the
 * invariant. The corrected design composes the concern <em>in</em> through
 * {@link SubmissionListener} rather than layering it <em>on</em>, and the
 * decorator was removed.
 *
 * <p>A rule that has been broken once and is guarded only by someone
 * remembering it will be broken again — the more so because the violating
 * change is always the convenient one. So the count is asserted.
 *
 * <p><strong>What this test cannot do</strong>, stated so it is not
 * over-trusted: it counts {@code implements OrderExecutor} across production
 * source. It cannot tell a legitimate implementation from a bad one, and it
 * would not catch a third executor that reached the interface indirectly. It
 * catches the shape the project actually produced, which is a new class
 * declaring the interface directly.
 *
 * <p>Full record: {@code .planning/paper-trading-h-vst-integration.md};
 * the architectural statement lives in {@code docs/architecture.md} §3.
 */
final class OrderExecutorImplementationCountTest {

    /** The two, by name, so a swap is as visible as an addition. */
    private static final List<String> EXPECTED =
            List.of("ExchangeOrderExecutor.java", "PaperBroker.java");

    private static Path repoRoot() {
        // The test runs with the module directory as its working directory.
        Path here = Path.of("").toAbsolutePath();
        while (here != null && !Files.isDirectory(here.resolve(".planning"))) {
            here = here.getParent();
        }
        assertTrue(here != null, "could not locate the repository root from " + Path.of("").toAbsolutePath());
        return here;
    }

    @Test
    @DisplayName("exactly two production classes implement OrderExecutor")
    void exactlyTwoProductionImplementations() throws IOException {
        Path java = repoRoot().resolve("java");
        List<Path> found;
        try (Stream<Path> walk = Files.walk(java)) {
            found =
                    walk.filter(Files::isRegularFile)
                            .filter(p -> p.toString().endsWith(".java"))
                            // Production source only. Test doubles are free to
                            // implement it; the invariant is about the shipped graph.
                            .filter(p -> p.toString().replace('\\', '/').contains("/src/main/"))
                            .filter(OrderExecutorImplementationCountTest::declaresOrderExecutor)
                            .sorted()
                            .collect(Collectors.toList());
        }

        List<String> names =
                found.stream().map(p -> p.getFileName().toString()).sorted().toList();

        assertEquals(
                EXPECTED,
                names,
                () ->
                        "OrderExecutor must have exactly two production implementations "
                                + "(PaperBroker and ExchangeOrderExecutor). A new venue means a "
                                + "new ExchangeAdapter; a cross-cutting concern is composed in "
                                + "via an injectable collaborator such as SubmissionListener, "
                                + "never layered on as a third executor. See "
                                + "docs/architecture.md section 3. Found: "
                                + names);
    }

    /**
     * Matches an {@code implements} clause naming the interface, and nothing
     * else.
     *
     * <p>The first version of this also accepted {@code ", OrderExecutor"} and
     * {@code "OrderExecutor,"} anywhere in the file, meaning to catch a
     * multi-interface declaration. Run against the real tree it counted
     * {@code PaperTradingApp} and {@code Reconciler}, which merely <em>take</em>
     * an {@code OrderExecutor} as a method parameter — two false positives out
     * of four results. A guard that reports a violation where there is none
     * gets switched off, so the match is on the declaration itself.
     */
    private static final Pattern IMPLEMENTS_CLAUSE =
            Pattern.compile("implements\\s+[^{;]*\\bOrderExecutor\\b");

    private static boolean declaresOrderExecutor(Path file) {
        try {
            return IMPLEMENTS_CLAUSE.matcher(Files.readString(file)).find();
        } catch (IOException e) {
            throw new IllegalStateException("could not read " + file, e);
        }
    }
}
