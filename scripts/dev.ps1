param(
    [ValidateSet('setup', 'check', 'java', 'git')][string]$Task = 'check',
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$GitArguments
)
$ErrorActionPreference = 'Stop'
$repoPath = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
# A relative gitdir works in Windows and WSL and does not leak GIT_DIR into
# test subprocesses. Keep the common repository's worktree backlink untouched.
$gitPointerPath = Join-Path $repoPath '.git'
if (Test-Path -LiteralPath $gitPointerPath -PathType Leaf) {
    $gitPointer = (Get-Content -LiteralPath $gitPointerPath -Raw).Trim()
    if ($gitPointer -match '^gitdir: ([A-Za-z]:[/\\].+)$') {
        $gitDir = (Resolve-Path -LiteralPath $Matches[1]).Path
        $relativeGitDir = [System.IO.Path]::GetRelativePath($repoPath, $gitDir).Replace('\', '/')
        if ([System.IO.Path]::IsPathRooted($relativeGitDir)) {
            throw 'Worktree and Git directory must be on the same Windows drive.'
        }
        $pointerBytes = [System.Text.UTF8Encoding]::new($false).GetBytes("gitdir: $relativeGitDir`n")
        # FileMode.Open also works for Windows' hidden .git file.
        $pointerStream = [System.IO.File]::Open($gitPointerPath, 'Open', 'Write', 'Read')
        try {
            $pointerStream.Write($pointerBytes, 0, $pointerBytes.Length)
            $pointerStream.SetLength($pointerBytes.Length)
        } finally {
            $pointerStream.Dispose()
        }
    }
}
& wsl.exe -d Ubuntu-24.04 --cd $repoPath --exec bash -lc 'exec bash scripts/dev.sh "$@"' dev $Task @GitArguments
exit $LASTEXITCODE
