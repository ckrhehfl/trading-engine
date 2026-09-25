package engine.execution;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
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
     * Every {@code class}/{@code record}/{@code enum} whose {@code implements}
     * clause names {@code OrderExecutor} <strong>directly</strong>.
     *
     * <p><strong>A scanner rather than one regex, after four review rounds of
     * the regex being not-quite-right.</strong> Each round was a wider version
     * of the last, and the sequence is kept because the shape of the mistake is
     * the transferable part:
     *
     * <ol>
     *   <li>counted <em>files</em> — a third class inside an existing file
     *       passed;
     *   <li>counted only {@code class} — a {@code record} or {@code enum}
     *       passed;
     *   <li>matched {@code OrderExecutor} anywhere in the clause, so
     *       {@code implements Supplier<OrderExecutor>} counted as an
     *       implementation. That one fails in the <em>other</em> direction: it
     *       blocks a legitimate declaration rather than letting a violation
     *       through, and a guard that does that gets switched off.
     * </ol>
     *
     * <p>Generic depth and top-level commas are all it needs, so this is a small
     * state machine and not a Java parser. What it still cannot see is an
     * <em>indirect</em> implementation — a class implementing a sub-interface of
     * {@code OrderExecutor} — and that stays disclosed rather than chased,
     * because reaching it would need real type resolution.
     */
    private static final Pattern DECLARATION =
            Pattern.compile("\\b(?:class|record|enum)\\s+(\\w+)");

    /** The interface whose implementations are counted. */
    private static final String INTERFACE = "OrderExecutor";

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
                declaredImplementors(twoTopLevelClassesInOneFile),
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
                declaredImplementors(nested),
                "a nested implementation must be counted too");

        String recordAndEnum =
                """
                package engine.execution;
                record RecordExecutor(String id) implements OrderExecutor {
                }
                enum EnumExecutor implements OrderExecutor {
                    INSTANCE;
                }
                """;
        assertEquals(
                List.of("RecordExecutor", "EnumExecutor"),
                declaredImplementors(recordAndEnum),
                "a record and an enum can each implement the interface and define"
                        + " its four methods -- matching `class` alone let a third"
                        + " implementation declared either way pass");

        String subInterface =
                """
                interface FancyOrderExecutor extends OrderExecutor {
                }
                """;
        assertEquals(
                List.of(),
                declaredImplementors(subInterface),
                "a sub-interface cannot be instantiated, so it is not an"
                        + " implementation -- and what excludes it is the required"
                        + " `implements` keyword, since a sub-interface says"
                        + " `extends`");

        // **The round-3 finding, and it fails in the OTHER direction**: neither
        // declaration implements the interface, and counting them would block a
        // legitimate production class rather than let a violation through.
        String asTypeArgument =
                """
                import java.util.function.Supplier;
                public final class ExecutorFactory implements Supplier<OrderExecutor> {
                }
                final class Registry implements Map<String, OrderExecutor> {
                }
                """;
        assertEquals(
                List.of(),
                declaredImplementors(asTypeArgument),
                "OrderExecutor as a type ARGUMENT is not an implementation -- depth"
                        + " tracking is what separates the two, and splitting on every"
                        + " comma would read it out of Map's type arguments");

        String qualifiedAndAlongsideAnother =
                """
                public final class Both
                        implements AutoCloseable, engine.execution.OrderExecutor {
                }
                """;
        assertEquals(
                List.of("Both"),
                declaredImplementors(qualifiedAndAlongsideAnother),
                "a fully qualified name is the same type, and it may sit beside"
                        + " other interfaces");

        // **Listed FIRST, which is the shape that needs the top-level comma
        // split.** Found by a mutation: disabling the split left every other
        // case passing, because `bareName` takes the text after the LAST dot and
        // `AutoCloseable, engine.execution.OrderExecutor` still ends in the right
        // name. Put the interface first and that accident disappears.
        String listedFirst =
                """
                import java.util.function.Supplier;
                public final class FirstInList
                        implements OrderExecutor, Supplier<String> {
                }
                """;
        assertEquals(
                List.of("FirstInList"),
                declaredImplementors(listedFirst),
                "the interface may be the first of several, and splitting the"
                        + " clause on top-level commas is what makes that work");

        // **The missing direction**, and the one this whole test exists to
        // prevent: a real third implementation that simply carries a trailing
        // comment. Exact comparison read the type name as
        // "OrderExecutor /* note */" and skipped it. Reported on review.
        String withComments =
                """
                public final class Commented implements OrderExecutor /* the third */ {
                }
                final class LineCommented
                        implements OrderExecutor // trailing note
                {
                }
                """;
        assertEquals(
                List.of("Commented", "LineCommented"),
                declaredImplementors(withComments),
                "a comment in or after the implements clause must not hide a real"
                        + " implementation -- this is the direction that lets a"
                        + " violation through");

        String parameterOnly =
                """
                public final class Reconciler {
                    Reconciler(OrderExecutor executor, OrderStore store) {}
                    void run(OrderExecutor executor) {}
                }
                """;
        assertEquals(
                List.of(),
                declaredImplementors(parameterOnly),
                "taking one as a parameter is not implementing it -- the looser first"
                        + " matcher counted Reconciler and PaperTradingApp this way");
    }

    private static Stream<String> implementingClasses(Path file) {
        try {
            return declaredImplementors(Files.readString(file)).stream();
        } catch (IOException e) {
            throw new IllegalStateException("could not read " + file, e);
        }
    }

    /**
     * Visible for testing: the same scan over a source string.
     *
     * <p>Synthetic source is the only way this is provable. The obvious
     * experiment — add a third implementation to {@code PaperBroker.java} and
     * watch the test fail — cannot run, because a class implementing
     * {@code OrderExecutor} with no method bodies does not compile: the build
     * fails before the test does and demonstrates nothing.
     */
    static List<String> declaredImplementors(String src) {
        List<String> names = new ArrayList<>();
        Matcher declaration = DECLARATION.matcher(src);
        while (declaration.find()) {
            String clause = implementsClause(src, declaration.end());
            if (clause != null && topLevelTypes(clause).contains(INTERFACE)) {
                names.add(declaration.group(1));
            }
        }
        return names;
    }

    /**
     * The {@code implements} clause following a declaration, or {@code null} if
     * it has none. Stops at the body or the statement end, so a later
     * declaration's clause cannot be attributed to this one.
     */
    private static String implementsClause(String src, int from) {
        int body = src.length();
        for (int i = from; i < src.length(); i++) {
            char c = src.charAt(i);
            if (c == '{' || c == ';') {
                body = i;
                break;
            }
        }
        String head = COMMENT.matcher(src.substring(from, body)).replaceAll(" ");
        int at = head.indexOf("implements");
        return at < 0 ? null : head.substring(at + "implements".length());
    }

    /**
     * Java comments, stripped from the clause before it is parsed.
     *
     * <p>{@code implements OrderExecutor /* note *&#47; } left the type name as
     * {@code "OrderExecutor /* note *&#47;"}, which exact comparison misses — so a
     * real third implementation carrying a trailing comment would have gone
     * <strong>uncounted</strong>. That is the missing direction, and the one this
     * whole test exists to prevent. Reported on review.
     *
     * <p>Applied to the clause only, never to the whole file: a clause sits
     * between {@code implements} and the body and cannot contain a string
     * literal, so there is nothing for a naive {@code //} strip to damage. Over
     * the whole file a {@code "https://…"} literal would be truncated and could
     * merge lines into a phantom declaration.
     *
     * <p><strong>Two shapes remain unhandled, and are disclosed rather than
     * chased</strong>: a comment between {@code class} and its name
     * ({@code class /*x*&#47; Third}), which breaks the declaration pattern
     * itself, and a comment carrying the word {@code implements} before a body.
     * Both would need lexing rather than scanning, and a declaration written
     * either way is strange enough that the honest handling is to say so.
     */
    private static final Pattern COMMENT =
            Pattern.compile("/\\*.*?\\*/|//[^\\n]*", Pattern.DOTALL);

    /**
     * The clause's top-level type names, generics stripped.
     *
     * <p>Depth tracking is the whole point: {@code Map<String, OrderExecutor>}
     * is one top-level type named {@code Map}, and splitting on every comma
     * would read {@code OrderExecutor} out of its type arguments.
     */
    private static List<String> topLevelTypes(String clause) {
        List<String> types = new ArrayList<>();
        StringBuilder current = new StringBuilder();
        int depth = 0;
        for (char c : clause.toCharArray()) {
            if (c == '<') {
                depth++;
            } else if (c == '>') {
                depth--;
            } else if (c == ',' && depth == 0) {
                types.add(bareName(current.toString()));
                current.setLength(0);
            } else if (depth == 0) {
                current.append(c);
            }
        }
        types.add(bareName(current.toString()));
        return types;
    }

    /** {@code engine.execution.OrderExecutor} and {@code OrderExecutor} are one type. */
    private static String bareName(String type) {
        String trimmed = type.trim();
        int dot = trimmed.lastIndexOf('.');
        return dot < 0 ? trimmed : trimmed.substring(dot + 1);
    }
}
