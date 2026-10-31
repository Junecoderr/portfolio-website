// TB for JetBrains IDEs: `tb lsp` through LSP4IJ, and highlighting from the
// VS Code extension's TextMate grammar. Build with: gradle buildPlugin
plugins {
    id("org.jetbrains.kotlin.jvm") version "2.1.20"
    id("org.jetbrains.intellij.platform") version "2.19.0"
}

group = "lang.tb"
version = providers.gradleProperty("pluginVersion").get()

repositories {
    mavenCentral()
    intellijPlatform {
        defaultRepositories()
    }
}

dependencies {
    intellijPlatform {
        intellijIdeaCommunity(providers.gradleProperty("platformVersion"))
        bundledPlugin("org.jetbrains.plugins.textmate")
        plugin("com.redhat.devtools.lsp4ij", providers.gradleProperty("lsp4ijVersion").get())
    }
}

kotlin {
    jvmToolchain(21)
}

intellijPlatform {
    pluginConfiguration {
        version = providers.gradleProperty("pluginVersion")
        ideaVersion {
            sinceBuild = providers.gradleProperty("sinceBuild")
            untilBuild = provider { null }
        }
    }
}

// The TextMate bundle is the VS Code extension's grammar, copied next to the
// plugin's jars (TextMate reads bundles from a folder, not from a jar).
val vscode = layout.projectDirectory.dir("../vscode")
tasks.prepareSandbox {
    from(vscode) {
        include("package.json", "language-configuration.json", "syntaxes/**")
        into(intellijPlatform.projectName.map { "$it/textmate" })
    }
}
