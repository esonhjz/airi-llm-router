/**
 * Airi Cross-Platform Hot-Patch Launcher
 * 
 * Automatically sniffs the frontend codebase, injects the Smart Retry Engine
 * to handle HTTP 429 & VRAM Throttling gracefully without showing red screens,
 * updates the .env to route traffic to the local gateway, and launches both
 * the Docker backend and the Frontend UI.
 */
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');

const routerDir = process.cwd();
let companionDir = null;

const candidates = [
  path.resolve(routerDir, '../airi'),
  path.resolve(routerDir, '../airi-companion')
];

for (const dir of candidates) {
  if (fs.existsSync(dir)) {
    companionDir = dir;
    break;
  }
}

console.log('🚀 [Launcher] Starting Airi Hot-Patch Launcher...');

// 1. Sniff frontend codebase
if (!companionDir) {
  console.error(`❌ [Launcher] Cannot find frontend codebase. Looked for 'airi' or 'airi-companion' next to 'airi-llm-router'.`);
  process.exit(1);
}
console.log(`✅ [Launcher] Found frontend codebase at: ${companionDir}`);

// 2. Modify .env to point traffic to local port 8000
const envPath = path.join(companionDir, '.env');
let envContent = '';
if (fs.existsSync(envPath)) {
  envContent = fs.readFileSync(envPath, 'utf8');
}

if (!envContent.includes('OPENAI_BASE_URL=http://localhost:8000/v1')) {
  envContent = envContent.replace(/OPENAI_BASE_URL=.*/g, 'OPENAI_BASE_URL=http://localhost:8000/v1');
  if (!envContent.includes('OPENAI_BASE_URL=')) {
    envContent += '\nOPENAI_BASE_URL=http://localhost:8000/v1';
  }
  fs.writeFileSync(envPath, envContent);
  console.log('✅ [Launcher] Patched frontend .env to route traffic to localhost:8000');
} else {
  console.log('✅ [Launcher] Frontend .env already configured correctly.');
}

// 3. Hot-inject the smartFetch interceptor
const patchCode = `
// [HOT-PATCH] Airi Smart Fetch Interceptor (Injected by airi-launcher.js)
if (typeof window !== 'undefined' && !window._smartFetchInjected) {
  window._smartFetchInjected = true;
  const originalFetch = window.fetch;
  window.fetch = async function(...args) {
    
    // Auto-Gateway Redirection
    let requestUrl = typeof args[0] === 'string' ? args[0] : (args[0] instanceof Request ? args[0].url : '');
    if (requestUrl && requestUrl.endsWith('/v1/chat/completions') && !requestUrl.includes('localhost:8000')) {
      const newUrl = 'http://localhost:8000/v1/chat/completions';
      if (typeof args[0] === 'string') {
        args[0] = newUrl;
      } else if (args[0] instanceof Request) {
        args[0] = new Request(newUrl, args[0]);
      }
      console.warn(\`[SmartFetch] 🔄 Auto-redirected LLM request to local gateway: \${newUrl}\`);
    }

    let retries = 0;
    while (true) {
      const response = await originalFetch.apply(this, args);
      if (response.status === 429) {
        const retryAfter = response.headers.get('Retry-After') || '3';
        const vramStatus = response.headers.get('X-VRAM-Status') || 'WARNING';
        const waitTime = parseInt(retryAfter, 10) * 1000;
        
        console.warn(\`[SmartFetch] 🚦 Gateway VRAM is \${vramStatus}. Request throttled. Retrying in \${waitTime}ms...\`);
        
        // Dispatch event for UI to show a friendly yellow warning instead of a red error screen
        window.dispatchEvent(new CustomEvent('airi-vram-warning', { 
          detail: { waitTime, vramStatus } 
        }));
        
        await new Promise(r => setTimeout(r, waitTime));
        retries++;
        continue; // Retry the original request
      }
      return response;
    }
  };
}
`;

const targets = [
  path.join(companionDir, 'apps/stage-web/src/main.ts'),
  path.join(companionDir, 'apps/stage-tamagotchi/src/main.ts'),
  path.join(companionDir, 'apps/stage-tamagotchi/src/renderer/main.ts')
];

for (const target of targets) {
  if (fs.existsSync(target)) {
    let content = fs.readFileSync(target, 'utf8');
    if (!content.includes('[HOT-PATCH] Airi Smart Fetch Interceptor')) {
      content = patchCode + '\n' + content;
      fs.writeFileSync(target, content);
      console.log(`✅ [Launcher] Injected SmartFetch Interceptor into ${path.basename(path.dirname(path.dirname(target)))}`);
    } else {
      console.log(`✅ [Launcher] SmartFetch Interceptor already present in ${path.basename(path.dirname(path.dirname(target)))}`);
    }
  }
}

// 4. Launch Docker backend
console.log('🐳 [Launcher] Starting Docker Gateway...');
const dockerCompose = spawn('docker', ['compose', 'up', '-d'], { cwd: routerDir, stdio: 'inherit' });

dockerCompose.on('close', (code) => {
  if (code !== 0) {
    console.error('❌ [Launcher] Failed to start Docker gateway.');
    process.exit(1);
  }
  
  console.log('✅ [Launcher] Docker Gateway started successfully.');
  
  // Check if node_modules/vite exists, if not run pnpm install
  const vitePath = path.join(companionDir, 'node_modules', 'vite');
  if (!fs.existsSync(vitePath)) {
    console.log('📦 [Launcher] node_modules not found. Running pnpm install (with official registry to prevent mirror 404s)...');
    
    // Inject mirrors for Electron and native binaries to prevent GitHub release download hangs
    const patchedEnv = Object.assign({}, process.env, {
      ELECTRON_MIRROR: 'https://npmmirror.com/mirrors/electron/',
      ELECTRON_BUILDER_BINARIES_MIRROR: 'https://npmmirror.com/mirrors/electron-builder-binaries/'
    });

    const installProcess = spawn('pnpm', ['install', '--registry=https://registry.npmjs.org'], { 
      cwd: companionDir, 
      stdio: 'inherit',
      shell: true,
      env: patchedEnv
    });

    installProcess.on('close', (installCode) => {
      if (installCode !== 0) {
        console.error('❌ [Launcher] pnpm install failed.');
        process.exit(1);
      }
      startFrontend();
    });
  } else {
    startFrontend();
  }

  let repairAttempted = false;

  function startFrontend() {
    console.log('🌐 [Launcher] Starting Frontend UI (stage-web)...');
    // 5. Launch Frontend UI
    const frontendProcess = spawn('pnpm', ['dev', '--filter', '@proj-airi/stage-web'], { 
      cwd: companionDir, 
      stdio: 'inherit',
      shell: true
    });
    
    frontendProcess.on('close', (code) => {
      if (code === 127 && !repairAttempted) {
        console.warn(`⚠️ [Launcher] Detected broken dependency tree (Exit 127). Initiating Self-Healing Protocol...`);
        repairAttempted = true;
        
        const patchedEnv = Object.assign({}, process.env, {
          ELECTRON_MIRROR: 'https://npmmirror.com/mirrors/electron/',
          ELECTRON_BUILDER_BINARIES_MIRROR: 'https://npmmirror.com/mirrors/electron-builder-binaries/'
        });

        const repairProcess = spawn('pnpm', ['install', '--registry=https://registry.npmjs.org'], { 
          cwd: companionDir, 
          stdio: 'inherit',
          shell: true,
          env: patchedEnv
        });

        repairProcess.on('close', (repairCode) => {
          if (repairCode === 0) {
            console.log(`✅ [Launcher] Self-Healing Complete. Restarting Frontend...`);
            startFrontend();
          } else {
            console.error(`❌ [Launcher] Auto-repair failed. Please run 'pnpm install' manually inside the frontend directory.`);
          }
        });
      } else {
        console.log(`Frontend process exited with code ${code}`);
      }
    });
  }
});
