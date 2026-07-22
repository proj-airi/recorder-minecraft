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
val nestedJarDirectory = layout.buildDirectory.dir("flashback-nested")
val extractFlashbackNested = tasks.register<Sync>("extractFlashbackNested") {
    from(flashbackJar.map { zipTree(it.asFile) })
    include("META-INF/jars/*.jar")
    eachFile { path = name }
    includeEmptyDirs = false
    into(nestedJarDirectory)
    doLast {
        check(fileTree(nestedJarDirectory).matching { include("*.jar") }.files.isNotEmpty()) {
            "Flashback artifact has no nested runtime libraries"
        }
    }
}
val nestedFlashbackJars = fileTree(nestedJarDirectory) { include("*.jar") }.apply {
    builtBy(extractFlashbackNested)
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
    modLocalRuntime(nestedFlashbackJars)

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
