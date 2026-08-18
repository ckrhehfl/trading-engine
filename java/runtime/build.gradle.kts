plugins {
    java
}

java {
    toolchain {
        languageVersion.set(JavaLanguageVersion.of(21))
    }
}

dependencies {
    implementation(project(":oms"))
    implementation(project(":risk"))
    implementation(project(":schemas"))
    implementation(project(":execution"))
    // Only for engine.exchange.ExchangeException, the shared error type for
    // anything exchange-communication-related -- not BingXAdapter/BingXSigner
    // (those are for authenticated write endpoints; BingXPriceFeed only ever
    // calls a public, unauthenticated one). See BingXPriceFeed's Javadoc.
    implementation(project(":exchange"))
    // 2.18.9, not the 2.18.2 used elsewhere in this repo (schemas/risk/
    // exchange) -- left at 2.18.2 there deliberately; a repo-wide bump is
    // a separate follow-up. 2.18.2 is in the CVE-2026-54515 range
    // (case-insensitive deserialization can restore properties
    // @JsonIgnoreProperties should have excluded), fixed in 2.18.9.
    // Nothing in this codebase uses the vulnerable combination
    // (ACCEPT_CASE_INSENSITIVE_PROPERTIES + @JsonIgnoreProperties --
    // verified via repo-wide grep, zero hits); this module's own
    // MAPPER.readValue() calls (AccountLedgerStore.load,
    // AccountLedgerLock's lock-metadata parsing) are only ever on
    // internally-written JSON files, never on untrusted external network
    // input.
    implementation("com.fasterxml.jackson.core:jackson-databind:2.18.9")
    // Needed for AccountLedger/LedgerReservation/AccountLedgerLock's
    // Instant-typed fields to serialize directly -- plain jackson-databind
    // has no built-in Instant support. This is pulled in transitively on
    // this module's runtime/test classpath via :schemas, but Gradle's
    // implementation/api separation does not expose it on this module's
    // own compile classpath, so it must be declared here explicitly too.
    implementation("com.fasterxml.jackson.datatype:jackson-datatype-jsr310:2.18.9")
    implementation("org.slf4j:slf4j-api:2.0.16")
    // A real SLF4J binding for actual (non-test) execution -- every other
    // module in this repo only ever declares slf4j-simple as
    // testRuntimeOnly, which is correct for a library module (the app
    // that assembles the final classpath should choose the binding), but
    // `runtime` is where PaperTradingApp's real `main()` lives, so it is
    // that assembling app. Without this, SLF4J silently falls back to its
    // no-op logger for any real (non-test) run -- discovered when
    // PaperTradingApp produced zero visible log output outside of tests,
    // which defeats Task C/D's whole point of structured, observable
    // per-tick/per-day logging during an actual paper-trading run.
    // `runtimeOnly` (not `implementation`), matching the SLF4J API-vs-
    // binding separation already used everywhere else in this repo.
    runtimeOnly("org.slf4j:slf4j-simple:2.0.16")

    testImplementation(platform("org.junit:junit-bom:5.11.4"))
    testImplementation("org.junit.jupiter:junit-jupiter")
    testRuntimeOnly("org.junit.platform:junit-platform-launcher")
    testRuntimeOnly("org.slf4j:slf4j-simple:2.0.16")
}

tasks.test {
    useJUnitPlatform()
}

// A real, committed way to launch PaperTradingApp -- not the Gradle
// `application` plugin (deliberately, per PaperTradingApp's own Javadoc:
// its default working directory wouldn't match this project's
// repo-root-relative path assumptions for the signal file/reports
// directory). A plain `JavaExec` task with `workingDir` set explicitly to
// the repo root sidesteps that without pulling in the plugin's other
// defaults. Zero behavior/logic change to PaperTradingApp itself -- this
// only adds a way to invoke its existing, already-reviewed `main()`.
// Used by `scripts/paper-trading-watchdog.sh` so a restart after a real
// process death (e.g. the tmux server itself dying, observed for real
// during this project's own local-run phase) doesn't depend on an
// ephemeral, session-scoped scratch file to reconstruct the classpath.
tasks.register<JavaExec>("runPaperTradingApp") {
    group = "application"
    description = "Runs engine.runtime.PaperTradingApp.main() with the real runtime classpath."
    mainClass.set("engine.runtime.PaperTradingApp")
    classpath = sourceSets["main"].runtimeClasspath
    workingDir = rootDir.parentFile // java/ -> repo root
    standardOutput = System.out
    errorOutput = System.err
}
