plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.enkore.mixer"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.enkore.mixer"
        minSdk = 21
        targetSdk = 34
        versionCode = 1
        versionName = "1.0.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    // Escaneo de QR sin depender de Google Play Services (funciona en
    // cualquier Android viejo, tenga o no GMS actualizado).
    implementation("com.journeyapps:zxing-android-embedded:4.3.0")
}
