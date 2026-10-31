package lang.tb.jetbrains

import com.intellij.execution.configurations.GeneralCommandLine
import com.intellij.openapi.project.Project
import com.redhat.devtools.lsp4ij.LanguageServerFactory
import com.redhat.devtools.lsp4ij.server.OSProcessStreamConnectionProvider
import com.redhat.devtools.lsp4ij.server.StreamConnectionProvider

/** Starts `tb lsp` for .tb files (TB_PATH picks another `tb` program). */
class TbLanguageServerFactory : LanguageServerFactory {
    override fun createConnectionProvider(project: Project): StreamConnectionProvider =
        TbLanguageServer(project)
}

class TbLanguageServer(project: Project) : OSProcessStreamConnectionProvider() {
    init {
        val tb = System.getenv("TB_PATH")?.takeIf { it.isNotBlank() } ?: "tb"
        val commandLine = GeneralCommandLine(tb, "lsp")
            .withParentEnvironmentType(GeneralCommandLine.ParentEnvironmentType.CONSOLE)
        project.basePath?.let { commandLine.withWorkDirectory(it) }
        setCommandLine(commandLine)
    }
}
