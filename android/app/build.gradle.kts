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
        versionCode = 1
        versionName = "0.1"
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

dependencies {
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
}
