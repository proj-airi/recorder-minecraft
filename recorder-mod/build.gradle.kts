plugins {
    kotlin("jvm") version "2.4.10"
    id("fabric-loom") version "1.17.16"
}

group = "dev.mcdata"
version = "0.1.0+1.21.8"

repositories {
    maven("https://maven.supersanta.me/snapshots")
    maven("https://jitpack.io")
    mavenCentral()
}

dependencies {
    minecraft("com.mojang:minecraft:1.21.8")
    mappings(loom.officialMojangMappings())

    modImplementation("net.fabricmc:fabric-loader:0.19.3")
    modImplementation("net.fabricmc.fabric-api:fabric-api:0.136.1+1.21.8")
    modImplementation("net.fabricmc:fabric-language-kotlin:1.13.13+kotlin.2.4.10")

    // ServerReplay 3.0.1 bundles these modules at runtime. They stay external here.
    modImplementation("net.casualchampionships:arcade-event-registry:0.6.3-beta.43+1.21.8")
    modImplementation("net.casualchampionships:arcade-events-server:0.6.3-beta.43+1.21.8")
    // Runtime classes are nested in the required ServerReplay jar; this is compile-only so the
    // recorder does not publish a second copy of the replay framework.
    modCompileOnly("net.casualchampionships:arcade-replay:0.6.3-beta.43+1.21.8")

    testImplementation(kotlin("test"))
    testImplementation("org.junit.jupiter:junit-jupiter:6.1.2")
}

base {
    archivesName.set("mc-recorder-mod")
}

java {
    toolchain.languageVersion.set(JavaLanguageVersion.of(21))
    withSourcesJar()
}

kotlin {
    jvmToolchain(21)
}

tasks.processResources {
    inputs.property("version", project.version)
    filesMatching("fabric.mod.json") {
        expand("version" to project.version)
    }
}

tasks.test {
    useJUnitPlatform()
}
