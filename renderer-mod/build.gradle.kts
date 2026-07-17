plugins {
    id("fabric-loom") version "1.11-SNAPSHOT"
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

    modCompileOnly("maven.modrinth:flashback:${property("flashback_version")}")
    modLocalRuntime("maven.modrinth:flashback:${property("flashback_version")}")
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
