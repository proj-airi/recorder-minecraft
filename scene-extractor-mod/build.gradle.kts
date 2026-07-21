plugins {
    id("fabric-loom") version "1.11-SNAPSHOT"
}

group = "dev.mcdata"
version = property("mod_version") as String

base {
    archivesName.set("mc-recorder-scene-extractor")
}

repositories {
    maven("https://maven.supersanta.me/snapshots")
    maven("https://jitpack.io")
    mavenCentral()
}

dependencies {
    minecraft("com.mojang:minecraft:${property("minecraft_version")}")
    mappings(loom.officialMojangMappings())
    modImplementation("net.fabricmc:fabric-loader:${property("loader_version")}")
    modImplementation("net.fabricmc.fabric-api:fabric-api:${property("fabric_version")}")
    modImplementation("net.casualchampionships:arcade-replay:${property("arcade_version")}")

    testImplementation("org.junit.jupiter:junit-jupiter:5.11.4")
    testRuntimeOnly("org.junit.platform:junit-platform-launcher")
}

loom {
    runs {
        named("server") {
            runDir = providers.gradleProperty("mcRecorderSceneRunDir").orElse("run").get()
        }
    }
}

java {
    toolchain.languageVersion.set(JavaLanguageVersion.of(21))
    withSourcesJar()
}

tasks.withType<JavaCompile>().configureEach {
    options.release.set(21)
    options.compilerArgs.add("-Xlint:all")
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
