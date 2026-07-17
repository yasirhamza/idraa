# RiskFlow Troubleshooting Guide

This document captures critical lessons learned from troubleshooting sessions to prevent recurring issues and accelerate future development.

## Table of Contents
- [Docker & Container Issues](#docker--container-issues)
- [Static Asset Serving](#static-asset-serving)
- [CSS & Styling Problems](#css--styling-problems)
- [Frontend-Backend Integration](#frontend-backend-integration)
- [FAIR Analysis & Data Integration](#fair-analysis--data-integration)
- [Development Workflow](#development-workflow)

---

## Docker & Container Issues

### Container Exit Status 0 (Successful Exit)
**Problem**: Frontend container builds successfully but immediately exits with status 0.

**Root Cause**: Dockerfile CMD pointing to incorrect build output path.

**Solution**:
- Remix v2 generates `build/index.js`, not `build/server/index.js`
- Use `tsx server.ts` instead of `node build/index.js` for development flexibility
- Always verify build output structure: `ls -la build/` inside container

**Prevention**:
```dockerfile
# ✅ Correct - Use custom server with tsx
CMD ["tsx", "server.ts"]

# ❌ Incorrect - Wrong build path
CMD ["node", "build/server/index.js"]

# ❌ Incorrect - Direct node execution of build output
CMD ["node", "build/index.js"]
```

### Redis Security Warnings
**Problem**: "Possible SECURITY ATTACK detected" warnings in Redis logs.

**Solution**: Use proper authentication in connection string:
```yaml
# ✅ Correct
REDIS_URL=redis://:${REDIS_PASSWORD}@redis:6379

# ❌ Incorrect
REDIS_URL=redis://redis:6379
```

### Frontend ERR_EMPTY_RESPONSE Issue
**Problem**: Frontend container is running but returns ERR_EMPTY_RESPONSE when accessing http://localhost:3002

**Root Causes**:
1. Server binding to `localhost` instead of `0.0.0.0` inside container
2. Port mapping mismatch between Docker Compose and application
3. Health check testing wrong port

**Solution**:
1. **Fix server binding in server.ts**:
```typescript
// ✅ Correct - Bind to all interfaces
const port = process.env.PORT || 3000;
const host = process.env.HOST || '0.0.0.0';
app.listen(port, host, () =>
  console.log(`Server listening at http://${host}:${port}`)
);

// ❌ Wrong - Only accessible inside container
app.listen(port, () =>
  console.log(`Server listening at http://localhost:${port}`)
);
```

2. **Ensure port consistency in docker-compose.yml**:
```yaml
# ✅ Correct - Ports match
ports:
  - "3002:3002"
environment:
  - PORT=3002
healthcheck:
  test: ["CMD", "curl", "-f", "http://localhost:3002"]

# ❌ Wrong - Port mismatch
ports:
  - "3002:3000"  # External 3002 but internal 3000
```

3. **Recreate container (not just restart)**:
```bash
# Stop and remove container
docker-compose -f docker-compose.simple.yml stop riskflow-frontend
docker-compose -f docker-compose.simple.yml rm -f riskflow-frontend

# Recreate with new configuration
docker-compose -f docker-compose.simple.yml up -d riskflow-frontend
```

**Debugging Steps**:
1. Test from inside container: `docker exec riskflow-frontend curl http://localhost:3002`
2. Check logs for binding address: `docker logs riskflow-frontend | grep listening`
3. Verify port mapping: `docker-compose ps` (should show 3002->3002, not 3002->3000)
4. Check container health: Should show "(healthy)" not "(unhealthy)" or "(health: starting)"

**Prevention**:
- Always use `0.0.0.0` for server binding in containerized applications
- Keep internal and external ports consistent unless specifically needed
- Use environment variables for port configuration
- Test connectivity from both inside and outside container

---

## Static Asset Serving

### Critical Middleware Order Issue
**Problem**: CSS and JS assets return 404 or get caught by Remix route handler.

**Root Cause**: Express middleware order - Remix catch-all route (`app.all("*")`) executes before static file middleware.

**Critical Solution**:
```typescript
// ✅ CORRECT ORDER - Static middleware MUST come before route handlers
app.use("/build", express.static("public/build", {
  immutable: true,
  maxAge: "1y",
  setHeaders: (res, path) => {
    if (path.endsWith('.js')) {
      res.setHeader('Content-Type', 'application/javascript');
    } else if (path.endsWith('.css')) {
      res.setHeader('Content-Type', 'text/css');
    }
  }
}));

// ❌ WRONG - Route handler catches assets first
app.all("*", remixHandler);
app.use("/build", express.static("public/build"));
```

**Key Debugging Steps**:
1. Check container build directory: `docker exec container ls -la public/build/`
2. Test direct asset access: `curl http://localhost:3002/build/filename.js`
3. Monitor request logs to see what middleware handles each request
4. Verify asset hashes match between HTML and filesystem

### Asset Hash Mismatches
**Problem**: HTML references `root-XL7BDEUY.js` but filesystem has `root-JX44WBZA.js`.

**Cause**: Asset hashes change on every build due to content changes.

**Solution**: Always test with current build assets, not cached filenames from previous builds.

---

## CSS & Styling Problems

### External Stylesheets Not Loading
**Problem**: Remix generates CSS files but they don't load, causing unstyled content.

**Primary Solutions** (in order of preference):
1. **Fix static middleware** (preferred) - Resolve Express routing to serve CSS files
2. **Inline critical CSS** (backup) - Add essential styles directly in HTML head
3. **Component-level CSS** (alternative) - Move styles to individual components

**Inline CSS Approach** (when static serving fails):
```typescript
// In root.tsx <head>
<style dangerouslySetInnerHTML={{
  __html: `
    /* Critical inline styles with !important to override */
    .h-full { height: 100% !important; }
    .bg-gray-50 { background-color: #f9fafb !important; }
    /* ... other essential utilities */
  `
}} />
```

### Tailwind CSS Integration
**Key Points**:
- Tailwind CSS file is generated as `/build/_assets/tailwind-[hash].css`
- Must be served by static middleware, not caught by route handlers
- Verify CSS generation in build process: check `public/build/_assets/` directory
- Test CSS loading: `curl http://localhost:3002/build/_assets/tailwind-[hash].css`

---

## Frontend-Backend Integration

### API Connection Issues
**Problem**: Frontend can't connect to backend API.

**Docker Environment Variables**:
```yaml
# Frontend container
environment:
  - API_URL=http://riskflow-app:3000/api/v1  # Use container name, not localhost
```

**Network Connectivity**:
- Use Docker service names for inter-container communication
- External access: `localhost:3001` (backend), `localhost:3002` (frontend)
- Internal access: `riskflow-app:3000`, `riskflow-frontend:3000`

---

## FAIR Analysis & Data Integration

### Backend Monte Carlo Simulation Data Structure
**Critical Finding**: The backend ALREADY generates comprehensive FAIR analysis data including Monte Carlo simulation results, but the frontend was not properly extracting this data.

**Root Cause Analysis**:
- Backend services (`/app/src/services/monteCarloService.js`) generate full Monte Carlo simulations with 10,000+ iterations
- FAIR Engine (`/app/src/fair_cam/risk_engine.js`) produces complete statistical analysis including VaR, Expected Shortfall, and loss exceedance arrays
- Analysis routes (`/app/src/routes/analysis.js`) return comprehensive `calculationResults`
- Frontend integration (`/app/lib/db/analysis-runs.ts`) was only extracting basic summary values

**What Backend Actually Provides**:
```javascript
// Backend returns rich simulation data:
calculationResults = {
  baseRisk: { annualLossExpectancy, singleLossExpectancy, annualRateOfOccurrence },
  controlledRisk: { annualLossExpectancy, singleLossExpectancy, controlEffectiveness },
  riskMetrics: {
    confidenceInterval: { lower, upper },
    riskScore, impactCategory
  },
  sensitivity: [...], // Parameter sensitivity analysis
  monteCarloResults: {
    simulations: [...], // Raw simulation arrays
    lossExceedanceData: [...],
    valueAtRisk: { var50, var75, var90, var95, var99 },
    expectedShortfall: { es50, es75, es90, es95, es99 }
  }
}
```

**What Frontend Was Extracting**:
```typescript
// Frontend was only capturing:
const baselineRisk = backendResults?.baseRisk?.singleLossExpectancy || 1000000;
const controlledRisk = backendResults?.controlledRisk?.singleLossExpectancy || baselineRisk * 0.5;

// And then generating FAKE calculations:
const var95 = controlledRisk * 0.8;  // ❌ Fake calculation
const var99 = controlledRisk * 2.1;  // ❌ Fake calculation
```

**Solution**: Update frontend data extraction to capture full backend simulation results.

### Loss Exceedance Curve Data Requirements
**Problem**: Dashboard showed "incorrect" loss exceedance curves because they weren't based on actual Monte Carlo simulation data.

**RiskFlux Prototype Pattern**:
- Uses actual PyFAIR Monte Carlo results with 10,000 simulations
- Stores raw loss arrays: `[7961820, 7380610, 7153480, ...]`
- Calculates exceedance probabilities from array positions: `probability = (index + 1) / array.length`
- Shows both inherent and controlled risk distributions for comparison

**Current Fix Required**:
1. Extract `monteCarloResults.simulations` from backend response
2. Store raw simulation arrays in analysis results
3. Update visualization components to process actual simulation data
4. Implement proper before/after control comparisons

### Data Structure Enhancement Required
**Before** (Current Limited Structure):
```typescript
results: {
  baseline_risk: number;
  controlled_risk: number;
  risk_reduction: number;
  var_95: number;  // Fake calculation
  var_99: number;  // Fake calculation
}
```

**After** (Full Backend Integration):
```typescript
results: {
  baseline_risk: number;
  controlled_risk: number;
  risk_reduction: number;

  // Real Monte Carlo data from backend
  monte_carlo_results: {
    baseline_simulations: number[];     // Raw simulation array
    controlled_simulations: number[];   // Raw controlled simulation array
    iterations: number;
  };

  // Multi-level VaR from backend
  value_at_risk: {
    var_50: number;
    var_75: number;
    var_90: number;
    var_95: number;
    var_99: number;
  };

  // Expected shortfall from backend
  expected_shortfall: {
    es_50: number;
    es_75: number;
    es_90: number;
    es_95: number;
    es_99: number;
  };

  // Statistical analysis from backend
  loss_distribution: {
    min: number;
    max: number;
    mean: number;
    median: number;
    std_dev: number;
    percentiles: { ... };
  };
}
```

### Key Lessons Learned
1. **Backend Capability Verification**: Always verify what backend services actually provide before assuming limitations
2. **Data Flow Analysis**: Trace data from backend API response through frontend processing to identify extraction issues
3. **Mock vs Real Data**: Distinguish between calculated estimates and actual simulation results
4. **Prototype Comparison**: Use existing working prototypes (like RiskFlux) to validate data structure requirements
5. **Full Integration Testing**: Test complete data pipeline, not just individual components

### Quick Diagnostic Commands
```bash
# Check what backend actually returns
docker exec riskflow-app sh -c "grep -A 20 'calculationResults' /app/src/routes/analysis.js"

# Verify FAIR engine capabilities
docker exec riskflow-app sh -c "head -100 /app/src/fair_cam/risk_engine.js"

# Check stored analysis results structure
docker exec riskflow-mongodb mongosh --eval "db.getSiblingDB('riskflow_dev').riskanalysisruns.findOne({}, {results: 1})"

# Compare with RiskFlux prototype data
cat "<local>\RiskFlux\prototypes\actual_pyfair_data.json" | head -50
```

### Prevention Strategies
1. **Always verify backend capabilities** before building frontend workarounds
2. **Check existing prototypes** for data structure patterns
3. **Test data flow end-to-end** from API to visualization
4. **Store simulation metadata** (iterations, confidence levels) for verification

---

## Development Workflow

### Automated vs Manual Deployment
**Lesson**: When CI/CD pipeline exists, don't fall back to manual npm commands.

**Correct Approach**:
1. Use Docker Compose for consistent environment
2. Leverage existing automation infrastructure
3. Only use manual commands for debugging specific issues

**Docker Compose Commands**:
```bash
# Full environment
docker-compose -f docker-compose.simple.yml up -d

# Rebuild single service
docker-compose -f docker-compose.simple.yml build riskflow-frontend
docker-compose -f docker-compose.simple.yml up riskflow-frontend -d

# Debug container
docker-compose -f docker-compose.simple.yml exec riskflow-frontend sh
```

### Debugging Container Issues
**Essential Commands**:
```bash
# Check container status
docker-compose ps

# View logs
docker-compose logs riskflow-frontend --tail=20

# Execute commands in container
docker-compose exec riskflow-frontend sh -c "ls -la build/"

# Test connectivity
curl -s http://localhost:3002/build/manifest-[hash].js | head -5
```

---

## Prevention Checklist

### Before Making Changes
- [ ] Verify current system works: test frontend at `http://localhost:3002`
- [ ] Check container status: `docker-compose ps`
- [ ] Note current asset hashes from browser network tab
- [ ] Backup working configuration files

### After Making Changes
- [ ] Rebuild affected containers: `docker-compose build [service]`
- [ ] Test asset loading: CSS and JS files load correctly
- [ ] Verify styling: frontend appears professionally styled
- [ ] Check container logs for errors
- [ ] Test navigation and functionality

### Code Review Points
- [ ] Static middleware configured before route handlers
- [ ] Asset paths match build output structure
- [ ] Docker environment variables use container names
- [ ] Content-Type headers set for static assets
- [ ] CSS files accessible via direct URL test

---

## Quick Reference

### File Locations
- Frontend server config: `remix-prototype/server.ts`
- Docker Compose: `deployment/docker-compose.simple.yml`
- Frontend Dockerfile: `remix-prototype/Dockerfile.frontend`
- Build output: `remix-prototype/public/build/`

### Port Mapping
- Backend: `localhost:3001` → `riskflow-app:3000`
- Frontend: `localhost:3002` → `riskflow-frontend:3000`
- MongoDB: `localhost:27017`
- Redis: `localhost:6379`

### Critical Dependencies
- Express static middleware order
- Remix build output structure (`build/index.js`)
- Asset hash consistency between HTML and filesystem
- Docker service name resolution

---

## Emergency Recovery

If frontend becomes unstyled:

1. **Check asset serving**: `curl http://localhost:3002/build/_assets/tailwind-[hash].css`
2. **Verify middleware order** in `server.ts`
3. **Rebuild frontend container**: `docker-compose build riskflow-frontend`
4. **Check logs**: `docker-compose logs riskflow-frontend`
5. **Test direct asset access** for specific files referenced in HTML

This troubleshooting guide should be consulted whenever similar issues arise to prevent repeating lengthy debugging sessions.
