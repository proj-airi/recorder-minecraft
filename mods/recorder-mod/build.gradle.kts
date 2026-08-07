import java.net.HttpURLConnection
import java.net.URI
import java.security.MessageDigest
import java.util.zip.ZipFile
import java.util.jar.JarInputStream

plugins {
    kotlin("jvm") version "2.4.10"
    id("fabric-loom") version "1.17.16"
}

group = "dev.mcdata"
version = "0.1.0+1.21.8"

val minecraftVersion = "1.21.8"
val fabricLoaderVersion = "0.19.3"
val fabricApiVersion = "0.136.1+1.21.8"
val fabricLanguageKotlinVersion = "1.13.13+kotlin.2.4.10"
val serverReplayVersion = "3.0.1+1.21.8"
val serverReplayVersionId = "TbWIikrT"
val serverReplaySha256 = "5538e575bd8559f61aaae3f697ead6ca20bc7393614dd1af2690a1ba1247a524"
val serverReplayUrl =
    "https://cdn.modrinth.com/data/qCvSZ8ra/versions/$serverReplayVersionId/ServerReplay-3.0.1%2B1.21.8.jar"

repositories {
    maven("https://maven.supersanta.me/snapshots")
    maven("https://jitpack.io")
    mavenCentral()
}

dependencies {
    minecraft("com.mojang:minecraft:$minecraftVersion")
    mappings(loom.officialMojangMappings())

    modImplementation("net.fabricmc:fabric-loader:$fabricLoaderVersion")
    modImplementation("net.fabricmc.fabric-api:fabric-api:$fabricApiVersion")
    modImplementation("net.fabricmc:fabric-language-kotlin:$fabricLanguageKotlinVersion")

    // ServerReplay 3.0.1 bundles these modules at runtime. They stay external here.
    modImplementation("net.casualchampionships:arcade-event-registry:0.6.3-beta.43+1.21.8")
    modImplementation("net.casualchampionships:arcade-events-server:0.6.3-beta.43+1.21.8")
    // Runtime classes are nested in the required ServerReplay jar; this is compile-only so the
    // recorder does not publish a second copy of the replay framework.
    modCompileOnly("net.casualchampionships:arcade-replay:0.6.3-beta.43+1.21.8")

    include(implementation("com.google.protobuf:protobuf-java:4.33.2")!!)
    include(implementation("com.google.protobuf:protobuf-java-util:4.33.2")!!)
    include(implementation("com.google.protobuf:protobuf-kotlin:4.33.2")!!)

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

sourceSets.main {
    java.srcDir("../../apis/sdk/jvm")
}

kotlin {
    jvmToolchain(21)
    sourceSets.main {
        kotlin.srcDir("../../apis/sdk/jvm")
    }
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

val profileFabricLanguageKotlin = configurations.detachedConfiguration(
    dependencies.create("net.fabricmc:fabric-language-kotlin:$fabricLanguageKotlinVersion"),
).apply {
    isTransitive = false
}

val serverReplayFile = layout.buildDirectory.file("profile-dependencies/ServerReplay-$serverReplayVersion.jar")

fun sha256(file: java.io.File): String {
    val digest = MessageDigest.getInstance("SHA-256")
    file.inputStream().use { input ->
        val buffer = ByteArray(DEFAULT_BUFFER_SIZE)
        while (true) {
            val count = input.read(buffer)
            if (count < 0) break
            digest.update(buffer, 0, count)
        }
    }
    return digest.digest().joinToString("") { byte -> "%02x".format(byte) }
}

val downloadServerReplay = tasks.register("downloadServerReplay") {
    group = "distribution"
    description = "Download and verify the pinned ServerReplay Fabric mod"
    inputs.properties(
        "version" to serverReplayVersion,
        "versionId" to serverReplayVersionId,
        "url" to serverReplayUrl,
        "sha256" to serverReplaySha256,
    )
    outputs.file(serverReplayFile)
    doLast {
        val target = serverReplayFile.get().asFile
        if (!target.isFile || sha256(target) != serverReplaySha256) {
            target.parentFile.mkdirs()
            val connection = URI(serverReplayUrl).toURL().openConnection() as HttpURLConnection
            connection.connectTimeout = 30_000
            connection.readTimeout = 30_000
            connection.instanceFollowRedirects = true
            connection.inputStream.use { input ->
                target.outputStream().use { output -> input.copyTo(output) }
            }
        }
        check(sha256(target) == serverReplaySha256) {
            "ServerReplay checksum mismatch for ${target.name}"
        }
    }
}

val profileResources = layout.buildDirectory.dir("generated/profile-resources")
val generateRecordingProfileMetadata = tasks.register<Copy>("generateRecordingProfileMetadata") {
    group = "distribution"
    description = "Generate the recording profile Fabric metadata"
    inputs.properties(
        "version" to project.version,
        "minecraftVersion" to minecraftVersion,
        "fabricLoaderVersion" to fabricLoaderVersion,
        "fabricApiVersion" to fabricApiVersion,
    )
    from("src/profile/resources")
    into(profileResources)
    expand(
        "version" to project.version,
        "minecraft_version" to minecraftVersion,
        "fabric_loader_version" to fabricLoaderVersion,
        "fabric_api_version" to fabricApiVersion,
    )
}

val buildRecordingProfile = tasks.register<Jar>("buildRecordingProfile") {
    group = "distribution"
    description = "Build the self-contained recording profile Fabric JAR"
    dependsOn(tasks.remapJar, downloadServerReplay, generateRecordingProfileMetadata)
    archiveBaseName.set("recorder-profile")
    archiveVersion.set(project.version.toString())
    destinationDirectory.set(layout.buildDirectory.dir("distributions"))
    duplicatesStrategy = DuplicatesStrategy.FAIL
    from(profileResources)
    from(tasks.remapJar.flatMap { it.archiveFile }) {
        into("META-INF/jars")
        rename { "recorder-minecraft.jar" }
    }
    from(serverReplayFile) {
        into("META-INF/jars")
        rename { "server-replay.jar" }
    }
    from(profileFabricLanguageKotlin) {
        into("META-INF/jars")
        rename { "fabric-language-kotlin.jar" }
    }
}

fun readModMetadata(jar: java.io.InputStream): String {
    JarInputStream(jar).use { input ->
        while (true) {
            val entry = input.nextJarEntry ?: break
            if (entry.name == "fabric.mod.json") {
                return input.readBytes().toString(Charsets.UTF_8)
            }
        }
    }
    error("nested Fabric mod does not contain fabric.mod.json")
}

fun metadataValue(metadata: String, key: String): String {
    return Regex("\\\"$key\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"")
        .find(metadata)?.groupValues?.get(1)
        ?: error("nested Fabric mod metadata has no $key")
}

val verifyRecordingProfile = tasks.register("verifyRecordingProfile") {
    group = "verification"
    description = "Verify the production recording profile contains its required nested Fabric mods"
    dependsOn(buildRecordingProfile)
    doLast {
        val profile = buildRecordingProfile.get().archiveFile.get().asFile
        val expected = mapOf(
            "META-INF/jars/recorder-minecraft.jar" to ("recorder-minecraft" to project.version.toString()),
            "META-INF/jars/server-replay.jar" to ("server-replay" to serverReplayVersion),
            "META-INF/jars/fabric-language-kotlin.jar" to ("fabric-language-kotlin" to fabricLanguageKotlinVersion),
        )

        ZipFile(profile).use { zip ->
            val profileMetadata = zip.getInputStream(zip.getEntry("fabric.mod.json"))
                .bufferedReader().use { it.readText() }
            check(metadataValue(profileMetadata, "id") == "recorder-profile")
            check(metadataValue(profileMetadata, "version") == project.version.toString())

            for ((path, identity) in expected) {
                val entry = zip.getEntry(path) ?: error("recording profile misses $path")
                check(profileMetadata.contains("\"file\": \"$path\"")) {
                    "recording profile metadata does not list $path"
                }
                val metadata = zip.getInputStream(entry).use(::readModMetadata)
                check(metadataValue(metadata, "id") == identity.first) {
                    "$path contains ${metadataValue(metadata, "id")}, expected ${identity.first}"
                }
                check(metadataValue(metadata, "version") == identity.second) {
                    "$path has version ${metadataValue(metadata, "version")}, expected ${identity.second}"
                }
            }
        }
    }
}

tasks.register<Copy>("stageServerImage") {
    group = "distribution"
    description = "Stage the remapped recorder mod under a stable server-image filename"
    dependsOn(tasks.remapJar)
    from(tasks.remapJar)
    into(layout.buildDirectory.dir("server-image"))
    rename { "mc-recorder-mod.jar" }
}

tasks.register<Copy>("stageRecordingProfile") {
    group = "distribution"
    description = "Stage the versioned recording profile under a stable filename"
    dependsOn(verifyRecordingProfile)
    from(buildRecordingProfile)
    into(layout.buildDirectory.dir("recording-profile"))
    rename { "recorder-profile.jar" }
}
