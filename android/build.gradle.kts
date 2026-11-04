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
