// TB for VS Code: plain JavaScript, no bundler.
//
// Highlighting, snippets and the language configuration work on their own.
// Everything else runs the `tb` program from the setting tb.path:
//   - the language server (`tb lsp`, TB 3.0+) for diagnostics, completion,
//     hover, go-to-definition, formatting;
//   - TB: Run File (a terminal running `tb file.tb`);
//   - TB: Format (`tb fmt file.tb`);
//   - TB: Check (`tb check file.tb`, shown in the Problems panel);
//   - TB: Run Tests (`tb test` in a terminal).
// If `tb lsp` isn't available (older TB, or tb not on PATH) the extension
// says so once and keeps working without it.

"use strict";

const path = require("path");
const { execFile } = require("child_process");
const vscode = require("vscode");

let LanguageClient = null;
let CloseAction = null;
let ErrorAction = null;
try {
  ({ LanguageClient, CloseAction, ErrorAction } = require("vscode-languageclient/node"));
} catch (err) {
  // node_modules missing (running from a bare checkout): the server is skipped.
}

const LANGUAGE = "tb";
const SERVER_WARNING_KEY = "tb.serverWarningShown";

let client = null;
let output = null;
let diagnostics = null;
let terminal = null;
let serverWarned = false;

// --- settings ----------------------------------------------------------------

function settings() {
  return vscode.workspace.getConfiguration("tb");
}

function tbPath() {
  const configured = settings().get("path", "tb");
  // In an untrusted workspace only the user-level value counts.
  if (!vscode.workspace.isTrusted) {
    const inspected = settings().inspect("path") || {};
    return inspected.globalValue || inspected.defaultValue || "tb";
  }
  return configured || "tb";
}

function log(message) {
  if (!output) {
    output = vscode.window.createOutputChannel("TB");
  }
  output.appendLine(message);
}

// --- running tb ----------------------------------------------------------------

/** Run `tb args...` and resolve with {code, stdout, stderr}; never rejects. */
function runTb(args, options = {}) {
  return new Promise((resolve) => {
    const child = execFile(
      tbPath(),
      args,
      { cwd: options.cwd, maxBuffer: 16 * 1024 * 1024, env: process.env },
      (error, stdout, stderr) => {
        const code = error ? (typeof error.code === "number" ? error.code : -1) : 0;
        const spawnError = error && typeof error.code === "string" ? error : null; // ENOENT etc.
        resolve({ code, stdout: stdout || "", stderr: stderr || "", spawnError });
      }
    );
    if (options.input !== undefined && child.stdin) {
      child.stdin.end(options.input);
    }
  });
}

function explainSpawnError(err) {
  if (err && err.code === "ENOENT") {
    return `I couldn't find '${tbPath()}'. Install TB (https://code.tanisha.ceo/download.html) or set "tb.path" to where tb lives.`;
  }
  return err ? `${err.message}` : "";
}

async function tbVersion() {
  const result = await runTb(["--version"]);
  if (result.spawnError) {
    return { ok: false, reason: explainSpawnError(result.spawnError) };
  }
  const match = /TB\s+(\d+)\.(\d+)\.(\d+)/.exec(result.stdout);
  if (!match) {
    return { ok: false, reason: `'${tbPath()} --version' didn't answer like TB does.` };
  }
  return { ok: true, major: Number(match[1]), text: match[0] };
}

function activeTbEditor() {
  const editor = vscode.window.activeTextEditor;
  if (!editor || editor.document.languageId !== LANGUAGE) {
    vscode.window.showInformationMessage("Open a .tb file first.");
    return null;
  }
  return editor;
}

function workspaceFolderFor(document) {
  const folder = vscode.workspace.getWorkspaceFolder(document.uri);
  return folder ? folder.uri.fsPath : path.dirname(document.uri.fsPath);
}

function quoteForShell(text) {
  if (/^[\w./:\\-]+$/.test(text)) {
    return text;
  }
  return process.platform === "win32" ? `"${text.replace(/"/g, '""')}"` : `'${text.replace(/'/g, `'\\''`)}'`;
}

function getTerminal(cwd) {
  if (!terminal || terminal.exitStatus !== undefined) {
    terminal = vscode.window.createTerminal({ name: "TB", cwd });
  }
  return terminal;
}

// --- commands --------------------------------------------------------------------

async function runFile() {
  const editor = activeTbEditor();
  if (!editor) {
    return;
  }
  if (editor.document.isUntitled) {
    vscode.window.showInformationMessage("Save the file first, then TB can run it.");
    return;
  }
  if (settings().get("run.saveBeforeRun", true) && editor.document.isDirty) {
    await editor.document.save();
  }
  const file = editor.document.uri.fsPath;
  const cwd = path.dirname(file);
  const term = getTerminal(cwd);
  term.show(true);
  term.sendText(`${quoteForShell(tbPath())} ${quoteForShell(path.basename(file))}`);
}

async function runTests() {
  const editor = vscode.window.activeTextEditor;
  const cwd = editor && !editor.document.isUntitled
    ? workspaceFolderFor(editor.document)
    : (vscode.workspace.workspaceFolders || [])[0]?.uri.fsPath;
  if (!cwd) {
    vscode.window.showInformationMessage("Open a folder (or a .tb file) first; TB runs the *_test.tb files in it.");
    return;
  }
  const term = getTerminal(cwd);
  term.show(true);
  term.sendText(`${quoteForShell(tbPath())} test`);
}

async function formatFile() {
  const editor = activeTbEditor();
  if (!editor) {
    return;
  }
  if (client && client.isRunning && client.isRunning()) {
    await vscode.commands.executeCommand("editor.action.formatDocument");
    return;
  }
  // No server: ask `tb fmt` to format the text on stdin ("-") and replace the document.
  const document = editor.document;
  const result = await runTb(["fmt", "-"], { cwd: workspaceFolderFor(document), input: document.getText() });
  if (result.spawnError) {
    vscode.window.showErrorMessage(`TB: Format - ${explainSpawnError(result.spawnError)}`);
    return;
  }
  if (result.code !== 0) {
    const detail = (result.stderr || result.stdout).trim().split("\n")[0];
    const older = /don't know|couldn't find 'fmt'|isn't a TB file|I don't know the option/i.test(detail);
    vscode.window.showErrorMessage(
      older ? "TB: Format needs TB 3.0 or newer (`tb fmt`). Update TB from https://code.tanisha.ceo/download.html"
            : `TB: Format - ${detail}`
    );
    log(`tb fmt: ${result.stderr || result.stdout}`);
    return;
  }
  if (result.stdout === document.getText()) {
    vscode.window.setStatusBarMessage("TB: already formatted", 2000);
    return;
  }
  const whole = new vscode.Range(document.positionAt(0), document.positionAt(document.getText().length));
  await editor.edit((edit) => edit.replace(whole, result.stdout));
  vscode.window.setStatusBarMessage("TB: formatted", 2000);
}

/** Parse `file:line:col: error|warning: message` lines from `tb check`. */
function parseCheckOutput(text, fallbackFile) {
  const found = new Map();
  const re = /^(.*?):(\d+):(\d+):\s*(error|warning|info|hint):\s*(.*)$/;
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    const m = re.exec(line);
    if (!m) {
      continue;
    }
    const file = m[1] || fallbackFile;
    const lineNo = Math.max(0, Number(m[2]) - 1);
    const col = Math.max(0, Number(m[3]) - 1);
    const severity = {
      error: vscode.DiagnosticSeverity.Error,
      warning: vscode.DiagnosticSeverity.Warning,
      info: vscode.DiagnosticSeverity.Information,
      hint: vscode.DiagnosticSeverity.Hint,
    }[m[4]];
    const range = new vscode.Range(lineNo, col, lineNo, col + 1);
    const diag = new vscode.Diagnostic(range, m[5], severity);
    diag.source = "tb check";
    const key = path.resolve(path.dirname(fallbackFile), file);
    if (!found.has(key)) {
      found.set(key, []);
    }
    found.get(key).push(diag);
  }
  return found;
}

async function checkFile(documentArg) {
  const document = documentArg || (activeTbEditor() || {}).document;
  if (!document) {
    return;
  }
  if (document.isDirty && !documentArg) {
    await document.save();
  }
  if (document.isUntitled) {
    vscode.window.showInformationMessage("Save the file first, then TB can check it.");
    return;
  }
  const file = document.uri.fsPath;
  const result = await runTb(["check", file], { cwd: workspaceFolderFor(document) });
  if (result.spawnError) {
    vscode.window.showErrorMessage(`TB: Check - ${explainSpawnError(result.spawnError)}`);
    return;
  }
  const text = `${result.stdout}\n${result.stderr}`;
  const parsed = parseCheckOutput(text, file);
  if (!diagnostics) {
    diagnostics = vscode.languages.createDiagnosticCollection("tb");
  }
  diagnostics.delete(document.uri);
  if (parsed.size === 0 && result.code !== 0) {
    const detail = text.trim().split("\n")[0];
    const older = /don't know|couldn't find 'check'|I don't know the option/i.test(detail);
    vscode.window.showErrorMessage(
      older ? "TB: Check needs TB 3.0 or newer (`tb check`). Update TB from https://code.tanisha.ceo/download.html"
            : `TB: Check - ${detail}`
    );
    log(`tb check: ${text}`);
    return;
  }
  let total = 0;
  for (const [key, list] of parsed) {
    diagnostics.set(vscode.Uri.file(key), list);
    total += list.length;
  }
  vscode.window.setStatusBarMessage(
    total === 0 ? "TB: no problems found" : `TB: ${total} problem${total === 1 ? "" : "s"} (see the Problems panel)`,
    4000
  );
}

// --- language server ----------------------------------------------------------------

async function warnServerOnce(context, reason) {
  if (serverWarned) {
    return;
  }
  serverWarned = true;
  log(`Language server not started: ${reason}`);
  // Only nag once per machine; the Output channel keeps the details.
  if (context.globalState.get(SERVER_WARNING_KEY)) {
    vscode.window.setStatusBarMessage("TB: language server off (see Output > TB)", 6000);
    return;
  }
  const choice = await vscode.window.showWarningMessage(
    `TB highlighting works, but the language server didn't start: ${reason}`,
    "Open Settings",
    "Don't show again"
  );
  if (choice === "Open Settings") {
    vscode.commands.executeCommand("workbench.action.openSettings", "tb.path");
  } else if (choice === "Don't show again") {
    await context.globalState.update(SERVER_WARNING_KEY, true);
  }
}

async function startServer(context) {
  if (!settings().get("languageServer.enabled", true)) {
    return;
  }
  if (!LanguageClient) {
    warnServerOnce(context, "the extension's node_modules are missing (run `npm install` in editors/vscode).");
    return;
  }
  const version = await tbVersion();
  if (!version.ok) {
    warnServerOnce(context, version.reason);
    return;
  }
  if (version.major < 3) {
    warnServerOnce(context, `${version.text} has no 'tb lsp'. TB 3.0 or newer adds it (https://code.tanisha.ceo/download.html).`);
    return;
  }
  const serverOptions = {
    command: tbPath(),
    args: ["lsp", ...settings().get("languageServer.arguments", [])],
    options: { env: process.env },
  };
  const clientOptions = {
    documentSelector: [{ scheme: "file", language: LANGUAGE }, { scheme: "untitled", language: LANGUAGE }],
    synchronize: { fileEvents: vscode.workspace.createFileSystemWatcher("**/*.{tb,toml}") },
    outputChannelName: "TB Language Server",
    revealOutputChannelOn: 4, // Never
    errorHandler: {
      error: () => ({ action: ErrorAction.Continue, handled: true }),
      closed: () => {
        warnServerOnce(context, "'tb lsp' stopped unexpectedly (see Output > TB Language Server).");
        return { action: CloseAction.DoNotRestart, handled: true };
      },
    },
  };
  client = new LanguageClient("tb", "TB Language Server", serverOptions, clientOptions);
  try {
    await client.start();
    log(`Language server started: ${tbPath()} lsp (${version.text})`);
  } catch (err) {
    client = null;
    warnServerOnce(context, `'${tbPath()} lsp' failed to start: ${err && err.message ? err.message : err}`);
  }
}

async function stopServer() {
  if (client) {
    const c = client;
    client = null;
    try {
      await c.stop();
    } catch (err) {
      // already gone
    }
  }
}

async function restartServer(context) {
  await stopServer();
  serverWarned = false;
  await startServer(context);
  if (client) {
    vscode.window.setStatusBarMessage("TB: language server restarted", 3000);
  }
}

// --- activation ------------------------------------------------------------------------

function activate(context) {
  context.subscriptions.push(
    vscode.commands.registerCommand("tb.runFile", runFile),
    vscode.commands.registerCommand("tb.format", formatFile),
    vscode.commands.registerCommand("tb.check", () => checkFile()),
    vscode.commands.registerCommand("tb.test", runTests),
    vscode.commands.registerCommand("tb.restartServer", () => restartServer(context)),
    vscode.workspace.onDidSaveTextDocument((document) => {
      if (document.languageId === LANGUAGE && settings().get("check.onSave", false) && !client) {
        checkFile(document);
      }
    }),
    vscode.workspace.onDidChangeConfiguration((event) => {
      if (event.affectsConfiguration("tb.path") || event.affectsConfiguration("tb.languageServer")) {
        restartServer(context);
      }
    }),
    vscode.window.onDidCloseTerminal((closed) => {
      if (closed === terminal) {
        terminal = null;
      }
    })
  );
  context.subscriptions.push({ dispose: () => stopServer() });
  if (output) {
    context.subscriptions.push(output);
  }
  startServer(context);
}

function deactivate() {
  return stopServer();
}

module.exports = { activate, deactivate, parseCheckOutput };
