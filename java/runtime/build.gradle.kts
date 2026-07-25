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
    // exchange) -- 2.18.2 is in the CVE-2026-54515 range (case-insensitive
    // deserialization can restore properties @JsonIgnoreProperties should
    // have excluded), fixed in 2.18.9. Nothing in this codebase actually
    // uses the vulnerable combination (ACCEPT_CASE_INSENSITIVE_PROPERTIES
    // + @JsonIgnoreProperties -- verified via repo-wide grep, zero hits),
    // and this module only ever calls readTree() (untyped tree walking),
    // never readValue() -- but this is the one module in the repo parsing
    // untrusted external (BingX) JSON over the network, so patching costs
    // nothing and is worth doing here regardless. Left at 2.18.2 in the
    // other three modules deliberately, not bumped here -- see PR #27
    // review discussion for why a repo-wide, cross-module version bump
    // (all four build.gradle.kts, including java/exchange, which this
    // task was explicitly told not to touch) is a separate, dedicated
    // follow-up rather than folded into this task's diff.
    implementation("com.fasterxml.jackson.core:jackson-databind:2.18.9")
    implementation("org.slf4j:slf4j-api:2.0.16")

    testImplementation(platform("org.junit:junit-bom:5.11.4"))
    testImplementation("org.junit.jupiter:junit-jupiter")
    testRuntimeOnly("org.junit.platform:junit-platform-launcher")
    testRuntimeOnly("org.slf4j:slf4j-simple:2.0.16")
}

tasks.test {
    useJUnitPlatform()
}
