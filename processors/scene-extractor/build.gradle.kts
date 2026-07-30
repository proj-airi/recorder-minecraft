plugins {
    id("fabric-loom") version "1.17.16"
    application
}

group = "dev.mcdata"
version = property("extractor_version") as String

base {
    archivesName.set("mc-recorder-scene-extractor")
}

application {
    mainClass.set("dev.mcdata.scene.SceneExtractorCli")
    applicationName = "mc-recorder-scene-extractor"
    applicationDefaultJvmArgs = listOf(
        "-DmcRecorder.minecraftVersion=${project.property("minecraft_version")}",
        "-DmcRecorder.fabricLoaderVersion=${project.property("loader_version")}",
        "-DmcRecorder.fabricVersion=${project.property("fabric_version")}",
        "-DmcRecorder.fabricKotlinVersion=${project.property("fabric_kotlin_version")}",
        "-DmcRecorder.arcadeVersion=${project.property("arcade_version")}",
        "-DmcRecorder.extractorVersion=${project.version}",
    )
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
    modImplementation("net.fabricmc:fabric-language-kotlin:${property("fabric_kotlin_version")}")
    modImplementation("net.casualchampionships:arcade-replay:${property("arcade_version")}")
    implementation("com.google.protobuf:protobuf-java:4.33.2")
    implementation("com.google.protobuf:protobuf-java-util:4.33.2")

    testImplementation("org.junit.jupiter:junit-jupiter:6.1.2")
    testRuntimeOnly("org.junit.platform:junit-platform-launcher")
}

sourceSets.main {
    java.srcDir("../../apis/sdk/jvm")
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

tasks.test {
    useJUnitPlatform()
    systemProperty("mcRecorder.minecraftVersion", project.property("minecraft_version"))
    systemProperty("mcRecorder.fabricLoaderVersion", project.property("loader_version"))
    systemProperty("mcRecorder.fabricVersion", project.property("fabric_version"))
    systemProperty("mcRecorder.fabricKotlinVersion", project.property("fabric_kotlin_version"))
    systemProperty("mcRecorder.arcadeVersion", project.property("arcade_version"))
    systemProperty("mcRecorder.extractorVersion", project.version)
}
