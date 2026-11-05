plugins {
    id("com.android.application") version "9.5.0" apply false
}

// The Android Gradle Plugin pulls these onto the build classpath at older
// versions with published advisories. They run only at build time (the app
// ships none of them), and the plugin works with the patched releases.
buildscript {
    dependencies {
        constraints {
            classpath("org.bouncycastle:bcprov-jdk18on:1.86")
            classpath("org.jdom:jdom2:2.0.6.1")
            classpath("com.google.guava:guava:33.8.0-jre")
            classpath("org.bitbucket.b_c:jose4j:0.9.7")
            classpath("org.apache.commons:commons-lang3:3.21.0")
            classpath("org.jetbrains.kotlin:kotlin-gradle-plugin:2.4.21")
        }
    }
}

// AGP's lint, R8 and signing tool configurations of :app resolve their own
// copies of these libraries, which the buildscript constraints above don't
// reach. Build-time only; nothing here ships in the APK.
val patchedBuildToolLibraries = mapOf(
    "org.bouncycastle:bcprov-jdk18on" to "1.86",
    "org.jdom:jdom2" to "2.0.6.1",
    "com.google.guava:guava" to "33.8.0-jre",
    "org.bitbucket.b_c:jose4j" to "0.9.7",
    "org.apache.commons:commons-lang3" to "3.21.0",
    "org.apache.httpcomponents:httpclient" to "4.5.14",
)

subprojects {
    configurations.configureEach {
        resolutionStrategy.eachDependency {
            patchedBuildToolLibraries["${requested.group}:${requested.name}"]?.let { useVersion(it) }
        }
    }
}
