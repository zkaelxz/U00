plugins {
    id("com.android.application")
}

android {
    namespace = "app.baihe.shell"
    compileSdk = 34

    defaultConfig {
        applicationId = "app.baihe.shell"
        minSdk = 26
        targetSdk = 34
        // Each build offered as an update must be higher than the one it replaces.
        versionCode = (project.findProperty("baiheVersionCode") as String?)?.toInt() ?: 1
        versionName = "0.1"
    }

    buildTypes {
        release {
            // Signed with the debug key on purpose: Android refuses an update signed with a
            // different key, and every phone already runs a build signed with it. The build is
            // still not debuggable, so USB debugging cannot read the app's stored sign-in.
            signingConfig = signingConfigs.getByName("debug")
            isDebuggable = false
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

dependencies {
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20260814")
}
