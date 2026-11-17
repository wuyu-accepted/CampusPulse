import { spawn, spawnSync } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { delimiter, dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const repositoryRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const frontendRoot = join(repositoryRoot, 'src', 'frontend')
const temporaryRoot = mkdtempSync(join(tmpdir(), 'campus-pulse-e2e-'))
const frontendBuildRoot = join(temporaryRoot, 'dist')
const host = '127.0.0.1'
const apiPort = '8000'
const frontendPort = '4173'
const viteCli = join(frontendRoot, 'node_modules', 'vite', 'bin', 'vite.js')
const dockerImage = `campus-pulse-e2e:${process.pid}`
const dockerContainer = `campus-pulse-e2e-${process.pid}`
const dockerProbe = spawnSync('docker', ['version'], { stdio: 'ignore' })
const dockerAvailable = !dockerProbe.error && dockerProbe.status === 0
const children = []
let stopping = false

function start(label, command, args, options) {
  const child = spawn(command, args, {
    ...options,
    detached: process.platform !== 'win32',
    stdio: 'inherit',
  })
  children.push(child)
  child.once('error', (error) => {
    console.error(`[e2e:${label}] failed to start: ${error.message}`)
    void stop(1)
  })
  child.once('exit', (code, signal) => {
    if (stopping) return
    console.error(`[e2e:${label}] exited before Playwright shutdown (code=${code}, signal=${signal})`)
    void stop(code || 1)
  })
  return child
}

async function waitForUrl(url, timeoutMs) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url)
      if (response.ok) return
    } catch {
      // The container may accept connections shortly after Docker starts it.
    }
    await new Promise((resolveWait) => setTimeout(resolveWait, 250))
  }
  throw new Error(`[e2e:backend] readiness timed out for ${url}`)
}

function terminate(child, signal) {
  if (!child.pid || child.exitCode !== null || child.signalCode !== null) return
  if (process.platform === 'win32') {
    const args = ['/pid', String(child.pid), '/t']
    if (signal === 'SIGKILL') args.push('/f')
    spawnSync('taskkill', args, { stdio: 'ignore' })
    return
  }
  try {
    process.kill(-child.pid, signal)
  } catch (error) {
    if (error.code === 'EPERM') child.kill(signal)
    else if (error.code !== 'ESRCH') throw error
  }
}

async function stop(exitCode = 0) {
  if (stopping) return
  stopping = true
  if (dockerAvailable) {
    spawnSync('docker', ['stop', '--time', '5', dockerContainer], { stdio: 'ignore' })
    spawnSync('docker', ['rm', '--force', dockerContainer], { stdio: 'ignore' })
  }
  for (const child of children) terminate(child, 'SIGTERM')
  await Promise.race([
    Promise.all(children.map((child) => new Promise((resolveChild) => {
      if (child.exitCode !== null || child.signalCode !== null) resolveChild()
      else child.once('exit', resolveChild)
    }))),
    new Promise((resolveTimeout) => setTimeout(resolveTimeout, 5_000)),
  ])
  for (const child of children) terminate(child, 'SIGKILL')
  if (dockerAvailable) spawnSync('docker', ['image', 'rm', '--force', dockerImage], { stdio: 'ignore' })
  rmSync(temporaryRoot, { recursive: true, force: true })
  process.exit(exitCode)
}

const frontendEnvironment = {
  ...process.env,
  CAMPUS_PULSE_API_TARGET: `http://${host}:${apiPort}`,
  CAMPUS_PULSE_VITE_CACHE_DIR: join(temporaryRoot, 'vite-cache'),
}

if (dockerAvailable) {
  const backendBuild = spawnSync(
    'docker',
    ['build', '--file', 'Dockerfile', '--tag', dockerImage, '.'],
    { cwd: repositoryRoot, stdio: 'inherit' },
  )
  if (backendBuild.error || backendBuild.status !== 0) {
    if (backendBuild.error) console.error(`[e2e:backend-build] ${backendBuild.error.message}`)
    rmSync(temporaryRoot, { recursive: true, force: true })
    process.exit(backendBuild.status || 1)
  }
}

const frontendBuild = spawnSync(
  process.execPath,
  [
    viteCli, 'build', '--configLoader', 'runner',
    '--outDir', frontendBuildRoot, '--emptyOutDir',
  ],
  { cwd: frontendRoot, env: frontendEnvironment, stdio: 'inherit' },
)
if (frontendBuild.error || frontendBuild.status !== 0) {
  if (frontendBuild.error) console.error(`[e2e:frontend-build] ${frontendBuild.error.message}`)
  if (dockerAvailable) spawnSync('docker', ['image', 'rm', '--force', dockerImage], { stdio: 'ignore' })
  rmSync(temporaryRoot, { recursive: true, force: true })
  process.exit(frontendBuild.status || 1)
}

if (dockerAvailable) {
  start(
    'backend',
    'docker',
    [
      'run', '--rm', '--name', dockerContainer,
      '--label', 'com.yvlan.e2e=workbench-persistence',
      '--publish', `${host}:${apiPort}:${apiPort}`,
      '--volume', `${temporaryRoot}:/var/lib/campus-pulse`,
      '--env', 'CAMPUS_PULSE_BIND_HOST=0.0.0.0',
      '--env', 'CAMPUS_PULSE_ALLOW_UNAUTHENTICATED_NETWORK=true',
      '--env', 'CAMPUS_PULSE_RUNTIME_ADAPTER=disabled',
      '--env', 'CAMPUS_PULSE_LIVE_LLM_ENABLED=false',
      '--env', 'CAMPUS_PULSE_WORKBENCH_DB=/var/lib/campus-pulse/workbench.sqlite3',
      dockerImage,
      '/usr/local/bin/python', '-m', 'backend.campus_pulse_app',
    ],
    { cwd: repositoryRoot, env: process.env },
  )
} else {
  const pythonCommand = process.env.CAMPUS_PULSE_E2E_PYTHON || (process.platform === 'win32' ? 'python' : 'python3')
  start(
    'backend-local',
    pythonCommand,
    ['-m', 'backend.campus_pulse_app'],
    {
      cwd: repositoryRoot,
      env: {
        ...process.env,
        PYTHONPATH: [join(repositoryRoot, 'src'), process.env.PYTHONPATH].filter(Boolean).join(delimiter),
        CAMPUS_PULSE_BIND_HOST: host,
        CAMPUS_PULSE_ALLOW_UNAUTHENTICATED_NETWORK: 'true',
        CAMPUS_PULSE_RUNTIME_ADAPTER: 'disabled',
        CAMPUS_PULSE_LIVE_LLM_ENABLED: 'false',
        CAMPUS_PULSE_WORKBENCH_DB: join(temporaryRoot, 'workbench.sqlite3'),
      },
    },
  )
}

try {
  await waitForUrl(`http://${host}:${apiPort}/campus-pulse/v1/health`, 60_000)
  if (dockerAvailable) {
    const containerId = spawnSync(
      'docker',
      ['inspect', '--format', '{{.Id}}', dockerContainer],
      { encoding: 'utf8' },
    ).stdout.trim()
    console.error(`[e2e:backend] Docker container ${dockerContainer} (${containerId.slice(0, 12)}) is ready`)
  } else {
    console.error('[e2e:backend] Docker unavailable; using the local Python environment')
  }
} catch (error) {
  console.error(error.message)
  await stop(1)
}

start(
  'frontend',
  process.execPath,
  [viteCli, 'preview', '--host', host, '--port', frontendPort, '--strictPort', '--outDir', frontendBuildRoot],
  { cwd: frontendRoot, env: frontendEnvironment },
)

process.once('SIGINT', () => void stop(130))
process.once('SIGTERM', () => void stop(0))
process.once('SIGHUP', () => void stop(0))

await new Promise(() => {})
