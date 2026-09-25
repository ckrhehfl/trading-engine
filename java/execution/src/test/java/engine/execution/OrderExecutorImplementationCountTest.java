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

    /**
     * The two, by <strong>class</strong> name, so a swap is as visible as an
     * addition.
     *
     * <p><strong>Class, not file</strong> — the first version compared file
     * names and could be defeated by putting a third implementation inside an
     * existing file. Java allows a second non-public top-level class per file,
     * and a nested one needs no permission at all; either way
     * {@code Matcher.find()} returned one {@code true} for the file and the
     * list still matched. A guard on the wrong unit is not a guard. Reported
     * on review of PR #207.
     */
    private static final List<String> EXPECTED =
            List.of("ExchangeOrderExecutor", "PaperBroker");

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
        List<String> names;
        try (Stream<Path> walk = Files.walk(java)) {
            names =
                    walk.filter(Files::isRegularFile)
                            .filter(p -> p.toString().endsWith(".java"))
                            // Production source only. Test doubles are free to
                            // implement it; the invariant is about the shipped graph.
                            .filter(p -> p.toString().replace('\\', '/').contains("/src/main/"))
                            .flatMap(OrderExecutorImplementationCountTest::implementingClasses)
                            .sorted()
                            .collect(Collectors.toList());
        }

        assertEquals(
                EXPECTED,
                names,
                () ->
                        "OrderExecutor must have exactly two production implementations "
                                + "(PaperBroker and ExchangeOrderExecutor). A new venue means a "
                                + "new ExchangeAdapter; a cross-cutting concern is composed in "
                                + "via an injectable collaborator such as SubmissionListener, "
                                + "never layered on as a third executor. See "
                                + "docs/architecture.md section 3. Found classes: "
                                + names);
    }

    /**
     * Captures the name of every class whose {@code implements} clause names
     * the interface.
     *
     * <p>Two corrections live in this one pattern, both found by running it:
     *
     * <ul>
     *   <li>it must match an {@code implements} <em>clause</em>, not the
     *       interface name anywhere in the file. A looser first version counted
     *       {@code PaperTradingApp} and {@code Reconciler}, which merely
     *       <em>take</em> an {@code OrderExecutor} as a method parameter — two
     *       false positives out of four results, and a guard that reports a
     *       violation where there is none gets switched off;
     *   <li>it must capture the <em>class</em>, not just answer yes/no per
     *       file, or a third implementation added to an existing file passes.
     * </ul>
     */
    private static final Pattern IMPLEMENTING_CLASS =
            Pattern.compile(
                    "\\bclass\\s+(\\w+)[^{;]*?\\bimplements\\b[^{;]*?\\bOrderExecutor\\b");

    @Test
    @DisplayName("the matcher counts CLASSES, so a third one hidden in an existing file is caught")
    void theMatcherCountsClassesNotFiles() {
        // **Proved on synthetic source rather than by editing production code.**
        // The obvious experiment -- add a third implementation to
        // PaperBroker.java and watch the test fail -- cannot run: a class
        // implementing OrderExecutor with no method bodies does not compile, so
        // the build fails before the test does and proves nothing. The regex is
        // what is under test, not javac.
        String twoTopLevelClassesInOneFile =
                """
                package engine.execution;
                public final class PaperBroker implements OrderExecutor {
                }
                final class SneakyThirdExecutor implements OrderExecutor {
                }
                """;
        assertEquals(
                List.of("PaperBroker", "SneakyThirdExecutor"),
                IMPLEMENTING_CLASS.matcher(twoTopLevelClassesInOneFile).results()
                        .map(m -> m.group(1))
                        .toList(),
                "a second top-level class in the same file must be counted -- this is"
                        + " legal Java and is exactly what the file-level check missed");

        String nested =
                """
                public final class Outer {
                    private static final class Inner implements OrderExecutor {
                    }
                }
                """;
        assertEquals(
                List.of("Inner"),
                IMPLEMENTING_CLASS.matcher(nested).results().map(m -> m.group(1)).toList(),
                "a nested implementation must be counted too");

        String parameterOnly =
                """
                public final class Reconciler {
                    Reconciler(OrderExecutor executor, OrderStore store) {}
                    void run(OrderExecutor executor) {}
                }
                """;
        assertEquals(
                List.of(),
                IMPLEMENTING_CLASS.matcher(parameterOnly).results().map(m -> m.group(1)).toList(),
                "taking one as a parameter is not implementing it -- the looser first"
                        + " matcher counted Reconciler and PaperTradingApp this way");
    }

    private static Stream<String> implementingClasses(Path file) {
        try {
            return IMPLEMENTING_CLASS.matcher(Files.readString(file)).results()
                    .map(m -> m.group(1))
                    .toList()
                    .stream();
        } catch (IOException e) {
            throw new IllegalStateException("could not read " + file, e);
        }
    }
}
