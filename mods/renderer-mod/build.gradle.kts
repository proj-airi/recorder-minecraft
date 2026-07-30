plugins {
    id("fabric-loom") version "1.17.16"
    `maven-publish`
}

group = "dev.mcdata"
version = property("mod_version") as String

base {
    archivesName.set("mc-recorder-renderer")
}

repositories {
    maven("https://api.modrinth.com/maven") {
        content {
            includeGroup("maven.modrinth")
        }
    }
}

val localFlashbackJar = providers.environmentVariable("MC_RECORDER_FLASHBACK_JAR").orNull
// Modrinth reuses the display version across Minecraft variants. The immutable
// version ID prevents 0.39.5 for a newer game from replacing the pinned
// Minecraft 1.21.8 artifact in Maven resolution.
val flashbackVersionId = property("flashback_version_id")
val flashbackDownload = configurations.detachedConfiguration(
    dependencies.create("maven.modrinth:flashback:$flashbackVersionId")
).apply {
    isTransitive = false
}
val flashbackJar = layout.file(providers.provider {
    if (localFlashbackJar.isNullOrBlank()) flashbackDownload.singleFile else file(localFlashbackJar)
})
val nestedJarDirectory = layout.buildDirectory.dir("flashback-nested").get().asFile
val requiredFlashbackNestedJars = listOf(
    "lattice-1.3.1.jar",
    "mixinconstraints-1.0.8.jar",
    "mixinsquared-fabric-0.3.7-beta.1.jar",
)
// NOTICE: Loom computes checksums for file dependencies while configuring the
// project, before task dependencies can produce their outputs. Materialize
// Flashback's nested runtime jars now so clean checkouts are resolvable.
// `https://github.com/FabricMC/fabric-loom/blob/ad89ffdb4e3c6fb1647e45cb5b3ca87ff1e77803/src/main/java/net/fabricmc/loom/configuration/mods/ModConfigurationRemapper.java#L288-L297`
project.sync {
    from(flashbackJar.map { zipTree(it.asFile) })
    include("META-INF/jars/*.jar")
    eachFile { path = name }
    includeEmptyDirs = false
    into(nestedJarDirectory)
}
val extractedNames = fileTree(nestedJarDirectory).matching { include("*.jar") }.files.map { it.name }.sorted()
check(extractedNames == requiredFlashbackNestedJars.sorted()) {
    "Flashback nested runtime libraries changed: $extractedNames"
}
val nestedFlashbackJars = requiredFlashbackNestedJars.map { name ->
    files(nestedJarDirectory.resolve(name))
}

// Resolve every prerequisite of runClient without launching Minecraft. Remote
// render workers run this before registering with the queue so transient Maven
// or asset-CDN failures cannot consume a render attempt.
tasks.register("prepareRendererRuntime") {
    group = "fabric"
    description = "Prepare the complete offline-capable renderer runtime"
    dependsOn("configureClientLaunch")
}

dependencies {
    minecraft("com.mojang:minecraft:${property("minecraft_version")}")
    mappings(loom.officialMojangMappings())
    modImplementation("net.fabricmc:fabric-loader:${property("loader_version")}")
    modImplementation("net.fabricmc.fabric-api:fabric-api:${property("fabric_version")}")
    modCompileOnly(files(flashbackJar))
    modLocalRuntime(files(flashbackJar))
    nestedFlashbackJars.forEach { modLocalRuntime(it) }
    include(implementation("com.google.protobuf:protobuf-java:4.33.2")!!)
    include(implementation("com.google.protobuf:protobuf-java-util:4.33.2")!!)

    testImplementation("org.junit.jupiter:junit-jupiter:6.1.2")
    testRuntimeOnly("org.junit.platform:junit-platform-launcher")
}

loom {
    splitEnvironmentSourceSets()

    mods {
        create("mc-recorder-renderer") {
            sourceSet(sourceSets["client"])
        }
    }
}

sourceSets.named("client") {
    java.srcDir("../../apis/sdk/jvm")
}

java {
    toolchain.languageVersion.set(JavaLanguageVersion.of(21))
    withSourcesJar()
}

tasks.withType<JavaCompile>().configureEach {
    options.release.set(21)
    options.compilerArgs.add("-Xlint:deprecation")
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
