plugins {
    id("fabric-loom") version "1.11.8"
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

dependencies {
    minecraft("com.mojang:minecraft:${property("minecraft_version")}")
    mappings(loom.officialMojangMappings())
    modImplementation("net.fabricmc:fabric-loader:${property("loader_version")}")
    modImplementation("net.fabricmc.fabric-api:fabric-api:${property("fabric_version")}")

    val localFlashbackJar = providers.environmentVariable("MC_RECORDER_FLASHBACK_JAR").orNull
    if (localFlashbackJar.isNullOrBlank()) {
        // Modrinth reuses the display version across Minecraft variants. The
        // immutable version ID prevents 0.39.1 for a newer game from replacing
        // the pinned Minecraft 1.21.8 artifact in Maven resolution.
        val flashbackVersionId = property("flashback_version_id")
        modCompileOnly("maven.modrinth:flashback:$flashbackVersionId")
        modLocalRuntime("maven.modrinth:flashback:$flashbackVersionId")
    } else {
        val nestedJarDirectory = layout.buildDirectory.dir("local-flashback-nested").get().asFile
        project.sync {
            from(zipTree(localFlashbackJar))
            include("META-INF/jars/*.jar")
            eachFile { path = name }
            includeEmptyDirs = false
            into(nestedJarDirectory)
        }
        modCompileOnly(files(localFlashbackJar))
        modLocalRuntime(files(localFlashbackJar))
        modLocalRuntime(fileTree(nestedJarDirectory) { include("*.jar") })
    }

    testImplementation("org.junit.jupiter:junit-jupiter:5.11.4")
    testRuntimeOnly("org.junit.platform:junit-platform-launcher")
}

loom {
    splitEnvironmentSourceSets()

    mods {
        create("mc-recorder-renderer") {
            sourceSet(sourceSets["client"])
        }
    }

    runs {
        named("client") {
            runDir = "run"
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
