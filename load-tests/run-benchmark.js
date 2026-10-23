/**
 * Relay Notification Platform — Reproducible Performance & Load Benchmark
 *
 * Measures:
 * 1. Fastify HTTP API acceptance throughput, status distribution, and latency percentiles (min, p50, p90, p95, p99, max).
 * 2. Asynchronous outbox delivery worker drain rate and batch processing latency.
 * 3. End-to-end concurrent pipeline processing (ingestion + worker delivery).
 *
 * Environment:
 * - Uses in-memory repositories (InMemoryTenantKeyRepository, InMemoryNotificationRepository)
 *   and fake provider adapters (FakeEmailProviderAdapter, FakePushProviderAdapter).
 * - Real loopback TCP networking using Node's keep-alive HTTP client.
 * - Zero external network calls or database credentials required.
 */

const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const { performance } = require("node:perf_hooks");

// Load compiled Relay application modules from dist/
const distPath = path.resolve(__dirname, "../dist");
if (!fs.existsSync(distPath)) {
  console.error("Error: dist/ directory not found. Please run 'npm run build' before executing load tests.");
  process.exit(1);
}

const { buildApp } = require(path.join(distPath, "app"));
const { InMemoryTenantKeyRepository, generateApiKey } = require(path.join(distPath, "auth"));
const { InMemoryNotificationRepository } = require(path.join(distPath, "notifications"));
const { FakeEmailProviderAdapter, FakePushProviderAdapter } = require(path.join(distPath, "providers"));
const { DeliveryWorker } = require(path.join(distPath, "worker"));

// Target planning figures from PRD & DESIGN.md
const PLANNING_TARGETS = {
  dailyVolume: 50_000_000,
  averageRps: 578.7, // ~579 req/s
  peakRps: 5787.0,   // ~5,787 req/s (10x peak multiplier)
  targetLatencyP95Ms: 500,
  targetLatencyP99Ms: 1000,
};

// Parse CLI flags
function parseArgs() {
  const args = process.argv.slice(2);
  let explicitOutput = null;
  const config = {
    mode: "full", // smoke | api | worker | pipeline | full
    requests: 2000,
    concurrency: 25,
    transport: "http", // http (TCP loopback) | inject (Fastify in-process)
    output: null,
    simulateFailure: null, // null | api | worker | pipeline
  };

  for (const arg of args) {
    if (arg.startsWith("--mode=")) config.mode = arg.split("=")[1];
    else if (arg.startsWith("--requests=")) config.requests = parseInt(arg.split("=")[1], 10);
    else if (arg.startsWith("--concurrency=")) config.concurrency = parseInt(arg.split("=")[1], 10);
    else if (arg.startsWith("--transport=")) config.transport = arg.split("=")[1];
    else if (arg.startsWith("--output=")) explicitOutput = path.resolve(arg.split("=")[1]);
    else if (arg.startsWith("--simulate-failure=")) config.simulateFailure = arg.split("=")[1];
  }

  const validModes = ["smoke", "api", "worker", "pipeline", "full"];
  if (!validModes.includes(config.mode)) {
    console.error(`Error: Invalid --mode='${config.mode}'. Must be one of: ${validModes.join(", ")}`);
    process.exit(1);
  }

  if (isNaN(config.requests) || config.requests <= 0) {
    console.error(`Error: Invalid --requests='${config.requests}'. Must be a positive integer.`);
    process.exit(1);
  }

  if (isNaN(config.concurrency) || config.concurrency <= 0) {
    console.error(`Error: Invalid --concurrency='${config.concurrency}'. Must be a positive integer.`);
    process.exit(1);
  }

  if (config.mode === "smoke") {
    config.requests = 100;
    config.concurrency = 5;
  }

  if (explicitOutput) {
    config.output = explicitOutput;
  } else if (config.simulateFailure) {
    config.output = path.resolve(__dirname, `../evidence/load-test-${config.simulateFailure}-failure.json`);
  } else if (config.mode === "smoke") {
    config.output = path.resolve(__dirname, "../evidence/load-test-smoke.json");
  } else if (config.mode === "full") {
    config.output = path.resolve(__dirname, "../evidence/load-test-results.json");
  } else {
    config.output = path.resolve(__dirname, `../evidence/load-test-${config.mode}.json`);
  }

  return config;
}

// Computes standard latency percentiles from a numeric array of milliseconds
function calculatePercentiles(latencies) {
  if (latencies.length === 0) {
    return { min: 0, p50: 0, p90: 0, p95: 0, p99: 0, max: 0, mean: 0 };
  }

  const sorted = [...latencies].sort((a, b) => a - b);
  const sum = sorted.reduce((acc, val) => acc + val, 0);

  const getP = (p) => {
    const idx = Math.min(Math.floor((p / 100) * sorted.length), sorted.length - 1);
    return Number(sorted[idx].toFixed(2));
  };

  return {
    min: Number(sorted[0].toFixed(2)),
    p50: getP(50),
    p90: getP(90),
    p95: getP(95),
    p99: getP(99),
    max: Number(sorted[sorted.length - 1].toFixed(2)),
    mean: Number((sum / sorted.length).toFixed(2)),
  };
}

// Sets up a clean test harness with synthetic credentials and opt-in preferences
async function createHarness(options = {}) {
  const tenantRepo = new InMemoryTenantKeyRepository();
  const notifRepo = new InMemoryNotificationRepository({
    hmacKey: Buffer.alloc(32, 9),
    activeKeyVersion: 1,
    previousKeys: new Map(),
  });

  const emailAdapter = new FakeEmailProviderAdapter({ initialMode: options.emailMode || "success" });
  const pushAdapter = new FakePushProviderAdapter({ initialMode: options.pushMode || "success" });

  const generatedKey = generateApiKey("benchkey01");
  const tenantId = "bench-tenant-1";

  tenantRepo.addRecord({
    publicKeyId: generatedKey.publicKeyId,
    tenantId,
    secretDigest: generatedKey.secretDigest,
    createdAt: new Date(),
    lastUsedAt: null,
    revokedAt: null,
    rotationValidUntil: null,
  });

  // Seed default opt-in preferences
  await notifRepo.setPreference(tenantId, "alice@example.com", "email", true);
  await notifRepo.setPreference(tenantId, "push-device-01", "push", true);

  const app = buildApp({
    tenantKeyRepository: tenantRepo,
    notificationRepository: notifRepo,
  });

  return {
    app,
    tenantRepo,
    notifRepo,
    emailAdapter,
    pushAdapter,
    tenantId,
    apiKey: options.overrideApiKey ?? generatedKey.rawKey,
  };
}

/**
 * Benchmark Part A: HTTP API Ingestion Acceptance
 */
async function benchmarkApiAcceptance(options) {
  const { requests, concurrency, transport, simulateFailure } = options;
  console.log(`\n======================================================`);
  console.log(`Benchmark 1: HTTP API Acceptance Ingestion`);
  console.log(`- Total Requests: ${requests}`);
  console.log(`- Concurrency:    ${concurrency}`);
  console.log(`- Transport:      ${transport.toUpperCase()} (Keep-Alive TCP loopback)`);
  console.log(`======================================================`);

  const harness = await createHarness({
    overrideApiKey: simulateFailure === "api" ? "invalid-key-simulated-failure" : undefined,
  });
  const { app, apiKey } = harness;

  let baseUrl = "";
  let port = 0;
  let httpAgent = null;

  if (transport === "http") {
    const address = await app.listen({ port: 0, host: "127.0.0.1" });
    port = app.server.address().port;
    baseUrl = `http://127.0.0.1:${port}`;
    httpAgent = new http.Agent({ keepAlive: true, maxSockets: concurrency + 5 });
  }

  const latencies = [];
  const statusCounts = {};
  let completedCount = 0;
  let errorCount = 0;

  const memBefore = process.memoryUsage();
  const startTime = performance.now();

  async function executeRequest(index) {
    const channel = index % 2 === 0 ? "email" : "push";
    const body = channel === "email"
      ? {
          channel: "email",
          recipient_id: "alice@example.com",
          subject: `Benchmark Event ${index}`,
          body: `Notification payload body for benchmark run item ${index}`,
        }
      : {
          channel: "push",
          recipient_id: "push-device-01",
          title: `Benchmark Alert ${index}`,
          body: `Push notification alert content ${index}`,
        };

    const idempotencyKey = `bench-key-${index}-${Date.now()}`;
    const payloadString = JSON.stringify(body);

    const reqStart = performance.now();

    if (transport === "inject") {
      const response = await app.inject({
        method: "POST",
        url: "/v1/notifications",
        headers: {
          "content-type": "application/json",
          "authorization": `Bearer ${apiKey}`,
          "idempotency-key": idempotencyKey,
        },
        payload: payloadString,
      });

      const reqDuration = performance.now() - reqStart;
      latencies.push(reqDuration);
      statusCounts[response.statusCode] = (statusCounts[response.statusCode] || 0) + 1;

      if (response.statusCode === 202) {
        completedCount++;
      } else {
        errorCount++;
      }
    } else {
      // Real HTTP request over local TCP socket
      await new Promise((resolve) => {
        const req = http.request(
          `${baseUrl}/v1/notifications`,
          {
            method: "POST",
            agent: httpAgent,
            headers: {
              "Content-Type": "application/json",
              "Content-Length": Buffer.byteLength(payloadString),
              "Authorization": `Bearer ${apiKey}`,
              "Idempotency-Key": idempotencyKey,
            },
          },
          (res) => {
            res.resume(); // Drain stream
            res.on("end", () => {
              const reqDuration = performance.now() - reqStart;
              latencies.push(reqDuration);
              statusCounts[res.statusCode] = (statusCounts[res.statusCode] || 0) + 1;

              if (res.statusCode === 202) {
                completedCount++;
              } else {
                errorCount++;
              }
              resolve();
            });
          }
        );

        req.on("error", (err) => {
          errorCount++;
          statusCounts["CONN_ERROR"] = (statusCounts["CONN_ERROR"] || 0) + 1;
          resolve();
        });

        req.write(payloadString);
        req.end();
      });
    }
  }

  // Work distribution worker pool
  let nextIndex = 0;
  async function worker() {
    while (true) {
      const idx = nextIndex++;
      if (idx >= requests) break;
      await executeRequest(idx);
    }
  }

  const workers = Array.from({ length: concurrency }, () => worker());
  await Promise.all(workers);

  const durationMs = performance.now() - startTime;
  const durationSec = durationMs / 1000;
  const throughputRps = Number((completedCount / durationSec).toFixed(2));
  const percentiles = calculatePercentiles(latencies);
  const memAfter = process.memoryUsage();

  if (transport === "http") {
    httpAgent.destroy();
    await app.close();
  }

  console.log(`Results:`);
  console.log(`- Duration:            ${durationSec.toFixed(2)}s`);
  console.log(`- Throughput:          ${throughputRps.toLocaleString()} req/s`);
  console.log(`- Status Distribution: ${JSON.stringify(statusCounts)}`);
  console.log(`- Latency (ms):        min=${percentiles.min}, p50=${percentiles.p50}, p95=${percentiles.p95}, p99=${percentiles.p99}, max=${percentiles.max}`);
  console.log(`- Heap Delta:          +${((memAfter.heapUsed - memBefore.heapUsed) / (1024 * 1024)).toFixed(2)} MB`);

  return {
    requests,
    concurrency,
    transport,
    durationSec,
    completedCount,
    errorCount,
    throughputRps,
    statusCounts,
    percentiles,
    heapDeltaMb: Number(((memAfter.heapUsed - memBefore.heapUsed) / (1024 * 1024)).toFixed(2)),
    failed: errorCount > 0,
    failureReason: errorCount > 0 ? `API benchmark encountered ${errorCount} error(s) or non-202 status code(s)` : null,
  };
}

/**
 * Benchmark Part B: Asynchronous Outbox Worker Throughput
 */
async function benchmarkWorkerDelivery(count = 2000, batchSize = 50, options = {}) {
  console.log(`\n======================================================`);
  console.log(`Benchmark 2: Asynchronous Outbox Worker Throughput`);
  console.log(`- Outbox Events:  ${count}`);
  console.log(`- Batch Size:     ${batchSize}`);
  console.log(`======================================================`);

  const harness = await createHarness({
    emailMode: options.simulateFailure === "worker" ? "permanent_failure" : "success",
    pushMode: options.simulateFailure === "worker" ? "permanent_failure" : "success",
  });
  const { notifRepo, emailAdapter, pushAdapter, tenantId, app, apiKey } = harness;

  // Pre-seed outbox events directly through the acceptance API
  console.log(`Pre-seeding ${count} notifications into outbox...`);
  for (let i = 0; i < count; i++) {
    await notifRepo.acceptNotification({
      tenantId,
      idempotencyKey: `worker-seed-${i}`,
      payload: {
        channel: i % 2 === 0 ? "email" : "push",
        recipient_id: i % 2 === 0 ? "alice@example.com" : "push-device-01",
        subject: `Outbox Seed ${i}`,
        body: `Body ${i}`,
        title: `Push Alert ${i}`,
      },
      fingerprintResult: {
        fingerprint: Buffer.alloc(32, 1),
        fingerprintHex: "01".repeat(32),
        canonicalBytes: Buffer.from("seed"),
        fingerprintFormatVersion: 1,
        hmacKeyVersion: 1,
      },
    });
  }

  const worker = new DeliveryWorker({
    repository: notifRepo,
    emailAdapter,
    pushAdapter,
    batchSize,
    leaseDurationMs: 30000,
  });

  const batchDurations = [];
  let totalProcessed = 0;
  let totalDelivered = 0;
  let totalRetried = 0;
  let totalFailed = 0;
  let totalSuppressed = 0;
  let totalFencedOut = 0;
  let totalTimeout = 0;

  const startTime = performance.now();

  while (totalProcessed < count) {
    const batchStart = performance.now();
    const batchResult = await worker.processBatch();
    const batchMs = performance.now() - batchStart;

    if (batchResult.claimedCount === 0) break;

    batchDurations.push(batchMs);
    totalProcessed += batchResult.processedCount;
    totalDelivered += batchResult.succeededCount;
    totalRetried += batchResult.retriedCount;
    totalFailed += batchResult.failedCount;
    totalSuppressed += batchResult.suppressedCount;
    totalFencedOut += batchResult.fencedOutCount;
    totalTimeout += batchResult.timeoutCount;
  }

  const durationSec = (performance.now() - startTime) / 1000;
  const throughputItemsPerSec = Number((totalDelivered / durationSec).toFixed(2));
  const batchPercentiles = calculatePercentiles(batchDurations);
  const errorCount = totalFailed + totalTimeout + totalFencedOut;
  const isFailed = totalDelivered !== count || errorCount > 0;
  const failureReason = isFailed
    ? `Worker delivery expected ${count} successful deliveries, but achieved ${totalDelivered} (errors: ${errorCount}, retries: ${totalRetried}, suppressions: ${totalSuppressed})`
    : null;

  console.log(`Results:`);
  console.log(`- Duration:            ${durationSec.toFixed(2)}s`);
  console.log(`- Delivered Rate:      ${throughputItemsPerSec.toLocaleString()} items/s`);
  console.log(`- Total Delivered:     ${totalDelivered} of ${count}`);
  console.log(`- Error Count:         ${errorCount}`);
  console.log(`- Retried Count:       ${totalRetried}`);
  console.log(`- Suppressed Count:    ${totalSuppressed}`);
  console.log(`- Batch Latency (ms):  p50=${batchPercentiles.p50}, p95=${batchPercentiles.p95}, mean=${batchPercentiles.mean}`);

  return {
    totalEvents: count,
    batchSize,
    totalDelivered,
    errorCount,
    retriedCount: totalRetried,
    suppressedCount: totalSuppressed,
    fencedOutCount: totalFencedOut,
    timeoutCount: totalTimeout,
    durationSec,
    throughputItemsPerSec,
    batchPercentiles,
    failed: isFailed,
    failureReason,
  };
}

/**
 * Benchmark Part C: End-to-End Pipeline (Concurrent Ingestion + Background Worker)
 */
async function benchmarkPipeline(count = 1000, concurrency = 20, options = {}) {
  console.log(`\n======================================================`);
  console.log(`Benchmark 3: End-to-End Pipeline (Ingestion + Worker)`);
  console.log(`- Ingest Requests: ${count}`);
  console.log(`- Concurrency:     ${concurrency}`);
  console.log(`======================================================`);

  const harness = await createHarness({
    emailMode: options.simulateFailure === "pipeline" ? "permanent_failure" : "success",
  });
  const { app, notifRepo, emailAdapter, pushAdapter, apiKey } = harness;

  const address = await app.listen({ port: 0, host: "127.0.0.1" });
  const port = app.server.address().port;
  const baseUrl = `http://127.0.0.1:${port}`;
  const httpAgent = new http.Agent({ keepAlive: true, maxSockets: concurrency + 5 });

  const worker = new DeliveryWorker({
    repository: notifRepo,
    emailAdapter,
    pushAdapter,
    batchSize: 50,
    leaseDurationMs: 30000,
  });

  // Start background worker polling every 50ms
  worker.start(50);

  const startTime = performance.now();
  let completedIngest = 0;
  let ingestErrors = 0;

  async function executeOne(index) {
    const bodyString = JSON.stringify({
      channel: "email",
      recipient_id: "alice@example.com",
      subject: `Pipeline Notice ${index}`,
      body: `Testing concurrent pipeline ${index}`,
    });

    await new Promise((resolve) => {
      const req = http.request(
        `${baseUrl}/v1/notifications`,
        {
          method: "POST",
          agent: httpAgent,
          headers: {
            "Content-Type": "application/json",
            "Content-Length": Buffer.byteLength(bodyString),
            "Authorization": `Bearer ${apiKey}`,
            "Idempotency-Key": `pipe-key-${index}-${Date.now()}`,
          },
        },
        (res) => {
          res.resume();
          res.on("end", () => {
            if (res.statusCode === 202) {
              completedIngest++;
            } else {
              ingestErrors++;
            }
            resolve();
          });
        }
      );

      req.on("error", () => {
        ingestErrors++;
        resolve();
      });

      req.write(bodyString);
      req.end();
    });
  }

  let nextIdx = 0;
  async function poolWorker() {
    while (true) {
      const idx = nextIdx++;
      if (idx >= count) break;
      await executeOne(idx);
    }
  }

  await Promise.all(Array.from({ length: concurrency }, () => poolWorker()));
  const ingestDurationSec = (performance.now() - startTime) / 1000;

  // Allow worker to drain remaining outbox items
  let attempts = 0;
  while (attempts < 100) {
    const pendingCount = Array.from(notifRepo.outboxEvents.values()).filter((e) => e.state !== "completed").length;
    if (pendingCount === 0) break;
    await new Promise((r) => setTimeout(r, 100));
    attempts++;
  }

  worker.stop();
  httpAgent.destroy();
  await app.close();

  const totalDurationSec = (performance.now() - startTime) / 1000;
  const deliveredCount = Array.from(notifRepo.notifications.values()).filter((n) => n.status === "sent").length;
  const unfinishedOutboxCount = Array.from(notifRepo.outboxEvents.values()).filter((e) => e.state !== "completed").length;
  const deliveryErrors = count - deliveredCount;
  const totalErrorCount = ingestErrors + deliveryErrors + unfinishedOutboxCount;
  const isFailed = completedIngest !== count || deliveredCount !== count || unfinishedOutboxCount > 0 || totalErrorCount > 0;
  const failureReason = isFailed
    ? `Pipeline failed: ingested ${completedIngest}/${count}, delivered ${deliveredCount}/${count}, unfinished outbox ${unfinishedOutboxCount}, errors ${totalErrorCount}`
    : null;

  console.log(`Results:`);
  console.log(`- Ingest Duration:     ${ingestDurationSec.toFixed(2)}s (${(completedIngest / ingestDurationSec).toFixed(2)} req/s)`);
  console.log(`- Ingest Completed:    ${completedIngest} of ${count}`);
  console.log(`- Ingest Errors:       ${ingestErrors}`);
  console.log(`- Total Duration:      ${totalDurationSec.toFixed(2)}s`);
  console.log(`- Final Delivered:     ${deliveredCount} of ${count}`);
  console.log(`- Unfinished Outbox:   ${unfinishedOutboxCount}`);

  return {
    count,
    concurrency,
    completedIngest,
    errorCount: totalErrorCount,
    ingestDurationSec,
    ingestRps: Number((completedIngest / ingestDurationSec).toFixed(2)),
    totalDurationSec,
    deliveredCount,
    fencedOutCount: 0,
    unfinishedOutboxCount,
    failed: isFailed,
    failureReason,
  };
}

/**
 * Main Runner
 */
async function main() {
  const options = parseArgs();

  console.log("=================================================================");
  console.log("  Relay Notification Platform — Performance Benchmark Runner    ");
  console.log("=================================================================");
  console.log(`Node.js Version: ${process.version}`);
  console.log(`Platform / OS:   ${process.platform} (${os.arch()}) — ${os.cpus()[0]?.model}`);
  console.log(`CPU Cores:       ${os.cpus().length}`);
  console.log(`Total RAM:       ${(os.totalmem() / (1024 ** 3)).toFixed(1)} GB`);
  console.log(`Execution Mode:  ${options.mode}`);
  console.log(`Database Double: In-Memory (InMemoryNotificationRepository)`);
  console.log(`Target Targets:  Average: ${PLANNING_TARGETS.averageRps} req/s | 10x Peak: ${PLANNING_TARGETS.peakRps} req/s`);

  const results = {
    metadata: {
      timestamp: new Date().toISOString(),
      mode: options.mode,
      simulateFailure: options.simulateFailure,
      nodeVersion: process.version,
      platform: process.platform,
      arch: os.arch(),
      cpuModel: os.cpus()[0]?.model,
      cpuCount: os.cpus().length,
      memoryTotalGb: Number((os.totalmem() / (1024 ** 3)).toFixed(1)),
      databaseEngine: "in-memory (InMemoryNotificationRepository)",
      planningTargets: PLANNING_TARGETS,
    },
    benchmarks: {},
  };

  try {
    if (options.mode === "smoke") {
      results.benchmarks.smoke = await benchmarkApiAcceptance({
        requests: 100,
        concurrency: 5,
        transport: "http",
        simulateFailure: options.simulateFailure,
      });
    } else if (options.mode === "api") {
      results.benchmarks.api = await benchmarkApiAcceptance(options);
    } else if (options.mode === "worker") {
      results.benchmarks.worker = await benchmarkWorkerDelivery(options.requests, 50, options);
    } else if (options.mode === "pipeline") {
      results.benchmarks.pipeline = await benchmarkPipeline(options.requests, options.concurrency, options);
    } else {
      // Full suite: Smoke -> API Acceptance (HTTP) -> Outbox Worker -> Pipeline
      console.log("\n[1/4] Running Initial Smoke Verification...");
      results.benchmarks.smoke = await benchmarkApiAcceptance({
        requests: 100,
        concurrency: 5,
        transport: "http",
        simulateFailure: options.simulateFailure,
      });

      console.log("\n[2/4] Running Sustained API Acceptance Benchmark (HTTP TCP Keep-Alive)...");
      results.benchmarks.apiHttp = await benchmarkApiAcceptance({
        requests: options.requests,
        concurrency: options.concurrency,
        transport: "http",
        simulateFailure: options.simulateFailure,
      });

      console.log("\n[3/4] Running Outbox Delivery Worker Benchmark...");
      results.benchmarks.worker = await benchmarkWorkerDelivery(options.requests, 50, options);

      console.log("\n[4/4] Running End-to-End Pipeline Saturation Benchmark...");
      results.benchmarks.pipeline = await benchmarkPipeline(Math.min(options.requests, 1000), options.concurrency, options);
    }

    // Save machine-readable output first so results are preserved even on benchmark failure
    const outputDir = path.dirname(options.output);
    if (!fs.existsSync(outputDir)) {
      fs.mkdirSync(outputDir, { recursive: true });
    }
    fs.writeFileSync(options.output, JSON.stringify(results, null, 2), "utf8");
    console.log(`\nBenchmark results successfully written to: ${options.output}`);

    // Print summary comparison against planning figures
    const apiThroughput = results.benchmarks.apiHttp?.throughputRps || results.benchmarks.api?.throughputRps || results.benchmarks.smoke?.throughputRps;
    console.log("\n=================================================================");
    console.log("  Benchmark Summary & Planning Comparison (In-Memory Prototype)  ");
    console.log("=================================================================");
    console.log(`Achieved API Ingestion Rate: ${apiThroughput?.toLocaleString() || "N/A"} req/s (in-memory prototype)`);
    console.log(`Planning Daily Average:      ${PLANNING_TARGETS.averageRps} req/s (50M notifications/day)`);
    console.log(`Planning 10x Peak Target:    ${PLANNING_TARGETS.peakRps} req/s`);

    if (apiThroughput >= PLANNING_TARGETS.averageRps) {
      console.log(`[INFO] In-memory API acceptance met or exceeded the 579 req/s daily average planning target (${apiThroughput} >= 578.7 req/s). Note: This is an in-memory measurement, not PostgreSQL capacity.`);
    } else {
      console.log(`[INFO] In-memory API acceptance did not meet the 579 req/s daily average planning target (${apiThroughput} < 578.7 req/s).`);
    }

    if (apiThroughput >= PLANNING_TARGETS.peakRps) {
      console.log(`[PASS] In-memory API acceptance met or exceeded the 5,787 req/s peak target.`);
    } else {
      console.log(`[INFO] Peak 5,787 req/s target was NOT met (achieved ${((apiThroughput / PLANNING_TARGETS.peakRps) * 100).toFixed(1)}% of peak target on a single core; horizontal scaling across multiple instances is required).`);
    }

    // Check for any benchmark failures
    let hasBenchmarkFailure = false;
    for (const [name, bm] of Object.entries(results.benchmarks)) {
      if (bm.failed) {
        hasBenchmarkFailure = true;
        console.error(`\n[FAILURE] Benchmark '${name}' failed: ${bm.failureReason}`);
      }
    }

    if (hasBenchmarkFailure) {
      console.error("\n[EXIT] Exiting with nonzero code (1) due to benchmark failure(s).");
      process.exit(1);
    }

  } catch (err) {
    console.error("Benchmark failed with error:", err);
    process.exit(1);
  }
}

main();
