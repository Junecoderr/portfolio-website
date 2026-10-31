package lang.tb.jetbrains

import com.intellij.ide.plugins.PluginManagerCore
import com.intellij.openapi.extensions.PluginId
import org.jetbrains.plugins.textmate.api.TextMateBundleProvider

/** Highlighting: the VS Code extension's TextMate grammar, shipped in the
 *  plugin's textmate/ folder (see prepareSandbox in build.gradle.kts). */
class TbTextMateBundleProvider : TextMateBundleProvider {
    override fun getBundles(): List<TextMateBundleProvider.PluginBundle> {
        val dir = PluginManagerCore.getPlugin(PluginId.getId("lang.tb.jetbrains"))
            ?.pluginPath?.resolve("textmate") ?: return emptyList()
        return listOf(TextMateBundleProvider.PluginBundle("TB", dir))
    }
}
