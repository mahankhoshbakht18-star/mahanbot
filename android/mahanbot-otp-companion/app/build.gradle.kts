plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.kapt)
    alias(libs.plugins.hilt)
}

android {
    namespace = "ir.mahan.mahanbototp"
    compileSdk = 35
    defaultConfig {
        applicationId = "ir.mahan.mahanbototp"
        minSdk = 28; targetSdk = 35; versionCode = 1; versionName = "1.0.0"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        buildConfigField("String", "RELAY_URL", "\"https://otp.mahanvip.ir\"")
    }
    flavorDimensions += "receiver"
    productFlavors {
        create("consent") { dimension = "receiver"; buildConfigField("String", "APP_FLAVOR", "\"consent\"") }
        create("private") { dimension = "receiver"; buildConfigField("String", "APP_FLAVOR", "\"private\"") }
    }
    buildTypes { getByName("debug") { isMinifyEnabled = false } }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
    kotlinOptions { jvmTarget = "17" }
    buildFeatures { compose = true; buildConfig = true }
    packaging { resources.excludes += "/META-INF/{AL2.0,LGPL2.1}" }
    applicationVariants.all {
        outputs.all {
            val flavor = flavorName
            (this as com.android.build.gradle.internal.api.BaseVariantOutputImpl).outputFileName =
                "MahanBot-OTP-Companion-1.0.0-${flavor}-debug.apk"
        }
    }
}

dependencies {
    implementation(platform(libs.compose.bom)); implementation(libs.compose.ui)
    implementation(libs.compose.material3); implementation(libs.compose.preview)
    debugImplementation(libs.compose.tooling); implementation(libs.activity.compose)
    implementation(libs.lifecycle.runtime); implementation(libs.hilt.android); kapt(libs.hilt.compiler)
    implementation(libs.hilt.work); kapt(libs.hilt.work.compiler)
    implementation(libs.room.runtime); implementation(libs.room.ktx); kapt(libs.room.compiler)
    implementation(libs.work.runtime); implementation(libs.datastore)
    implementation(libs.retrofit); implementation(libs.retrofit.moshi)
    implementation(libs.moshi.kotlin); implementation(libs.okhttp)
    "consentImplementation"(libs.auth.phone)
    testImplementation(libs.junit)
}
