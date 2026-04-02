<#
.SYNOPSIS
    Runtm development helper for Windows PowerShell.
    Mirrors the Makefile targets so Windows users don't need `make`.

.EXAMPLE
    .\scripts\dev.ps1 dev        # Start stack with hot-reload
    .\scripts\dev.ps1 test       # Run tests inside Docker
    .\scripts\dev.ps1 logs api   # Tail API logs
#>

param(
    [Parameter(Position = 0)]
    [ValidateSet(
        "dev", "dev-detach", "down", "restart", "logs", "ps",
        "test", "test-shared", "test-api",
        "lint", "format", "typecheck",
        "db-migrate", "db-reset",
        "prod", "prod-down", "prod-logs",
        "health", "health-detailed",
        "shell-api", "shell-worker",
        "help"
    )]
    [string]$Command = "help",

    [Parameter(Position = 1, ValueFromRemainingArguments)]
    [string[]]$Extra
)

$DevCompose  = "docker compose -f infra/docker-compose.dev.yml"
$ProdCompose = "docker compose -f infra/docker-compose.prod.yml"

function Invoke-Cmd($cmd) {
    Write-Host ">> $cmd" -ForegroundColor Cyan
    Invoke-Expression $cmd
    if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) {
        Write-Host "Command failed with exit code $LASTEXITCODE" -ForegroundColor Red
    }
}

switch ($Command) {
    # ── Development ──
    "dev"         { Invoke-Cmd "$DevCompose up --build" }
    "dev-detach"  { Invoke-Cmd "$DevCompose up --build -d" }
    "down"        { Invoke-Cmd "$DevCompose down"; Invoke-Cmd "$ProdCompose down" }
    "restart"     { Invoke-Cmd "$DevCompose down"; Invoke-Cmd "$DevCompose up --build" }
    "logs"        { $svc = if ($Extra) { $Extra[0] } else { "" }; Invoke-Cmd "$DevCompose logs -f $svc" }
    "ps"          { Invoke-Cmd "$DevCompose ps" }
    "shell-api"   { Invoke-Cmd "$DevCompose exec api bash" }
    "shell-worker"{ Invoke-Cmd "$DevCompose exec worker bash" }

    # ── Testing & Linting ──
    "test"        { Invoke-Cmd "$DevCompose exec api pytest /app/packages/shared/tests -v"
                    Invoke-Cmd "$DevCompose exec api pytest /app/packages/api/tests -v" }
    "test-shared" { Invoke-Cmd "$DevCompose exec api pytest /app/packages/shared/tests -v" }
    "test-api"    { Invoke-Cmd "$DevCompose exec api pytest /app/packages/api/tests -v" }
    "lint"        { Invoke-Cmd "$DevCompose exec api ruff check /app/packages" }
    "format"      { Invoke-Cmd "$DevCompose exec api ruff format /app/packages" }
    "typecheck"   { Invoke-Cmd "$DevCompose exec api mypy /app/packages/shared/runtm_shared --ignore-missing-imports"
                    Invoke-Cmd "$DevCompose exec api mypy /app/packages/api/runtm_api --ignore-missing-imports" }

    # ── Database ──
    "db-migrate"  { Invoke-Cmd "$DevCompose exec api alembic upgrade head" }
    "db-reset"    { Invoke-Cmd "$DevCompose down -v"; Invoke-Cmd "$DevCompose up --build" }

    # ── Production ──
    "prod"        { Invoke-Cmd "$ProdCompose up --build -d" }
    "prod-down"   { Invoke-Cmd "$ProdCompose down" }
    "prod-logs"   { $svc = if ($Extra) { $Extra[0] } else { "" }; Invoke-Cmd "$ProdCompose logs -f $svc" }

    # ── Health ──
    "health"          { try { (Invoke-RestMethod http://localhost:8000/health) | ConvertTo-Json } catch { Write-Host "API not reachable" -ForegroundColor Red } }
    "health-detailed" { try { (Invoke-RestMethod http://localhost:8000/health/detailed) | ConvertTo-Json } catch { Write-Host "API not reachable" -ForegroundColor Red } }

    # ── Help ──
    "help" {
        Write-Host ""
        Write-Host "Runtm Dev Helper" -ForegroundColor Green
        Write-Host "Usage: .\scripts\dev.ps1 <command> [args]" -ForegroundColor Gray
        Write-Host ""
        Write-Host "  Development" -ForegroundColor Yellow
        Write-Host "    dev            Start stack with hot-reload"
        Write-Host "    dev-detach     Start stack in the background"
        Write-Host "    down           Stop all services"
        Write-Host "    restart        Rebuild and restart"
        Write-Host "    logs [svc]     Tail logs (optionally filter by service)"
        Write-Host "    ps             Show running services"
        Write-Host "    shell-api      Shell into API container"
        Write-Host "    shell-worker   Shell into worker container"
        Write-Host ""
        Write-Host "  Testing" -ForegroundColor Yellow
        Write-Host "    test           Run full test suite"
        Write-Host "    test-shared    Run shared package tests"
        Write-Host "    test-api       Run API tests"
        Write-Host "    lint           Lint with ruff"
        Write-Host "    format         Auto-format with ruff"
        Write-Host "    typecheck      Run mypy"
        Write-Host ""
        Write-Host "  Database" -ForegroundColor Yellow
        Write-Host "    db-migrate     Run alembic migrations"
        Write-Host "    db-reset       Destroy volumes and recreate"
        Write-Host ""
        Write-Host "  Production" -ForegroundColor Yellow
        Write-Host "    prod           Start production stack"
        Write-Host "    prod-down      Stop production stack"
        Write-Host "    prod-logs      Tail production logs"
        Write-Host ""
        Write-Host "  Health" -ForegroundColor Yellow
        Write-Host "    health         Quick health check"
        Write-Host "    health-detailed  Detailed health (Postgres, Redis, queue)"
        Write-Host ""
    }
}
