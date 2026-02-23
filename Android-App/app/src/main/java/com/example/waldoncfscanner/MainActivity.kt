package com.example.waldoncfscanner

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.os.Bundle
import android.view.View
import android.widget.*
import androidx.appcompat.app.AppCompatActivity
import com.google.android.material.progressindicator.LinearProgressIndicator
import com.google.android.material.slider.Slider
import com.google.android.material.switchmaterial.SwitchMaterial
import kotlinx.coroutines.*
import kotlinx.coroutines.channels.Channel
import okhttp3.ConnectionPool
import okhttp3.Dns
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.Proxy
import java.net.Socket
import java.net.URLDecoder
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

class MainActivity : AppCompatActivity() {

    // UI Elements
    private lateinit var pageSetup: LinearLayout
    private lateinit var inputUri: EditText
    private lateinit var inputTargetSetup: EditText
    private lateinit var sliderPowerSetup: Slider
    private lateinit var tvPowerLabelSetup: TextView
    private lateinit var btnPaste: Button
    private lateinit var btnStartMain: Button
    private lateinit var switchDebug: SwitchMaterial

    private lateinit var pageScanner: LinearLayout
    private lateinit var tvActiveUri: TextView
    private lateinit var inputTargetLive: EditText
    private lateinit var inputPowerLive: EditText
    private lateinit var btnPauseResume: Button
    private lateinit var btnStopLive: Button
    private lateinit var btnCopyConfigs: Button
    private lateinit var btnCopyLogs: Button
    private lateinit var tvTargetProgress: TextView
    private lateinit var tvConfigs: TextView
    private lateinit var tvLogs: TextView
    private lateinit var logScrollView: ScrollView

    private lateinit var pbTcp: LinearProgressIndicator
    private lateinit var pbTls: LinearProgressIndicator
    private lateinit var pbSpeed: LinearProgressIndicator
    private lateinit var pbXray: LinearProgressIndicator

    // --- CRITICAL ANR FIX: Shared Resources ---
    // This prevents Android from crashing due to Thread/Memory explosion
    private val baseClient = OkHttpClient.Builder()
        .connectionPool(ConnectionPool(100, 5, TimeUnit.MINUTES))
        .build()

    // Dedicated network thread pool to protect the Main UI Thread
    private val scanDispatcher = Executors.newFixedThreadPool(150).asCoroutineDispatcher()

    // Engine States
    private var scannerScope: CoroutineScope? = null
    private var isScanning = false
    private var isPaused = false
    private var isDebugEnabled = false
    private val foundIps = mutableListOf<String>()
    private val generatedConfigs = mutableListOf<String>()
    private var activeUriString = ""

    // Live Tracking
    private val activeTcp = AtomicInteger(0)
    private val activeTls = AtomicInteger(0)
    private val activeSpeed = AtomicInteger(0)
    private val activeXray = AtomicInteger(0)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        // Bind Setup Page
        pageSetup = findViewById(R.id.pageSetup)
        inputUri = findViewById(R.id.inputUri)
        inputTargetSetup = findViewById(R.id.inputTargetSetup)
        sliderPowerSetup = findViewById(R.id.sliderPowerSetup)
        tvPowerLabelSetup = findViewById(R.id.tvPowerLabelSetup)
        btnPaste = findViewById(R.id.btnPaste)
        btnStartMain = findViewById(R.id.btnStartMain)
        switchDebug = findViewById(R.id.switchDebug)

        // Bind Scanner Page
        pageScanner = findViewById(R.id.pageScanner)
        tvActiveUri = findViewById(R.id.tvActiveUri)
        inputTargetLive = findViewById(R.id.inputTargetLive)
        inputPowerLive = findViewById(R.id.inputPowerLive)
        btnPauseResume = findViewById(R.id.btnPauseResume)
        btnStopLive = findViewById(R.id.btnStopLive)
        btnCopyConfigs = findViewById(R.id.btnCopyConfigs)
        btnCopyLogs = findViewById(R.id.btnCopyLogs)
        tvTargetProgress = findViewById(R.id.tvTargetProgress)
        tvConfigs = findViewById(R.id.tvConfigs)
        tvLogs = findViewById(R.id.tvLogs)
        logScrollView = findViewById(R.id.logScrollView)

        pbTcp = findViewById(R.id.pbTcp)
        pbTls = findViewById(R.id.pbTls)
        pbSpeed = findViewById(R.id.pbSpeed)
        pbXray = findViewById(R.id.pbXray)

        sliderPowerSetup.addOnChangeListener { _, value, _ ->
            tvPowerLabelSetup.text = "ENGINE POWER: ${value.toInt()}%"
        }

        btnPaste.setOnClickListener { pasteFromClipboard() }
        btnStartMain.setOnClickListener { initiateScan() }

        btnPauseResume.setOnClickListener { togglePause() }
        btnStopLive.setOnClickListener { stopScan() }
        btnCopyConfigs.setOnClickListener { copyToClipboard("Configs", generatedConfigs.joinToString("\n\n")) }
        btnCopyLogs.setOnClickListener { copyToClipboard("Logs", tvLogs.text.toString()) }
    }

    override fun onDestroy() {
        super.onDestroy()
        scanDispatcher.close() // Clean up thread pool
    }

    private fun appendLog(msg: String) {
        runOnUiThread {
            // Auto-truncate logs so the UI never slows down over time
            val currentText = tvLogs.text.toString()
            if (currentText.length > 8000) {
                tvLogs.text = currentText.substring(currentText.length - 4000)
            }
            tvLogs.append("$msg\n")
            logScrollView.post { logScrollView.fullScroll(ScrollView.FOCUS_DOWN) }
        }
    }

    private fun copyToClipboard(label: String, text: String) {
        if (text.isBlank()) return
        val clipboard = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        val clip = ClipData.newPlainText(label, text)
        clipboard.setPrimaryClip(clip)
        Toast.makeText(this, "$label copied to clipboard!", Toast.LENGTH_SHORT).show()
    }

    private fun pasteFromClipboard() {
        val clipboard = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        val item = clipboard.primaryClip?.getItemAt(0)?.text?.toString()
        if (item != null && item.startsWith("vless://")) {
            inputUri.setText(item)
            Toast.makeText(this, "URI Pasted!", Toast.LENGTH_SHORT).show()
        }
    }

    private fun initiateScan() {
        activeUriString = inputUri.text.toString().trim()
        if (!activeUriString.startsWith("vless://")) {
            Toast.makeText(this, "Valid VLESS URI required.", Toast.LENGTH_SHORT).show()
            return
        }

        pageSetup.visibility = View.GONE
        pageScanner.visibility = View.VISIBLE

        tvActiveUri.text = activeUriString
        inputTargetLive.setText(inputTargetSetup.text.toString())
        inputPowerLive.setText(sliderPowerSetup.value.toInt().toString())

        startEngine()
    }

    private fun startEngine() {
        isScanning = true
        isPaused = false
        isDebugEnabled = switchDebug.isChecked

        btnPauseResume.text = "⏸ PAUSE"
        btnPauseResume.setBackgroundColor(android.graphics.Color.parseColor("#FF9800"))

        inputTargetLive.isEnabled = false
        inputPowerLive.isEnabled = false

        foundIps.clear()
        generatedConfigs.clear()
        tvConfigs.text = ""
        tvLogs.text = "🚀 Starting Engine...\n"

        val power = inputPowerLive.text.toString().toIntOrNull() ?: 10

        scannerScope?.cancel()
        scannerScope = CoroutineScope(scanDispatcher + SupervisorJob())

        scannerScope?.launch {
            try {
                runScannerPipeline(power)
            } catch (e: CancellationException) {
                appendLog("⏹️ Workers terminated safely.")
            } catch (e: Exception) {
                appendLog("❌ Fatal Error: ${e.message}")
            }
        }

        scannerScope?.launch(Dispatchers.Main) {
            while (isActive) {
                pbTcp.progress = activeTcp.get()
                pbTls.progress = activeTls.get()
                pbSpeed.progress = activeSpeed.get()
                pbXray.progress = activeXray.get()
                delay(200)
            }
        }
    }

    private fun togglePause() {
        if (!isScanning) {
            startEngine()
            return
        }

        isPaused = !isPaused
        if (isPaused) {
            btnPauseResume.text = "▶ RESUME"
            btnPauseResume.setBackgroundColor(android.graphics.Color.parseColor("#4CAF50"))
            inputTargetLive.isEnabled = true
            inputPowerLive.isEnabled = true
            appendLog("⏸️ Engine Paused. You may change settings.")
        } else {
            btnPauseResume.text = "⏸ PAUSE"
            btnPauseResume.setBackgroundColor(android.graphics.Color.parseColor("#FF9800"))
            inputTargetLive.isEnabled = false
            inputPowerLive.isEnabled = false
            appendLog("▶️ Engine Resumed.")

            scannerScope?.cancel()
            startEngine()
        }
    }

    private fun stopScan() {
        isScanning = false
        isPaused = false
        scannerScope?.cancel()

        btnPauseResume.text = "🔄 RESTART"
        btnPauseResume.setBackgroundColor(android.graphics.Color.parseColor("#2196F3"))

        inputTargetLive.isEnabled = true
        inputPowerLive.isEnabled = true
        appendLog("⏹️ Scan Stopped manually.")
    }

    private suspend fun runScannerPipeline(powerPercent: Int) = coroutineScope {
        val ips = loadIpsFromAssets()
        if (ips.isEmpty()) {
            appendLog("❌ Failed to load ipv4.txt from assets!")
            return@coroutineScope
        }

        val activeSockets = (150 * (powerPercent / 100.0)).toInt().coerceAtLeast(5)
        val numTcpWorkers = (activeSockets * 0.70).toInt().coerceAtLeast(2)
        val numTlsWorkers = (activeSockets * 0.20).toInt().coerceAtLeast(1)
        val numSpeedWorkers = (activeSockets * 0.10).toInt().coerceAtLeast(1)
        val numXrayWorkers = 5

        withContext(Dispatchers.Main) {
            pbTcp.max = numTcpWorkers
            pbTls.max = numTlsWorkers
            pbSpeed.max = numSpeedWorkers
            pbXray.max = numXrayWorkers
            appendLog("⚙️ Armed workers: TCP:$numTcpWorkers TLS:$numTlsWorkers SPD:$numSpeedWorkers XRY:$numXrayWorkers")
        }

        val rawQueue = Channel<String>(numTcpWorkers * 2)
        val tcpQueue = Channel<String>(numTlsWorkers * 2)
        val xrayQueue = Channel<String>(numXrayWorkers * 2)

        launch {
            while (isActive && isScanning) {
                if (!isPaused) rawQueue.send(generateRandomIp(ips))
                delay(20)
            }
        }

        repeat(numTcpWorkers) {
            launch {
                for (ip in rawQueue) {
                    if (!isActive) break
                    while (isPaused) delay(200)

                    activeTcp.incrementAndGet()
                    if (checkTcp(ip)) tcpQueue.send(ip)
                    activeTcp.decrementAndGet()
                }
            }
        }

        repeat(numTlsWorkers + numSpeedWorkers) {
            launch {
                for (ip in tcpQueue) {
                    if (!isActive) break
                    while (isPaused) delay(200)

                    activeTls.incrementAndGet()
                    activeSpeed.incrementAndGet()
                    if (checkTlsAndSpeed(ip)) {
                        if (isDebugEnabled) appendLog("⚡ SPEED OK: $ip")
                        xrayQueue.send(ip)
                    }
                    activeTls.decrementAndGet()
                    activeSpeed.decrementAndGet()
                }
            }
        }

        repeat(numXrayWorkers) {
            launch {
                for (ip in xrayQueue) {
                    if (!isActive) break
                    while (isPaused) delay(200)

                    activeXray.incrementAndGet()
                    verifyWithXray(ip, activeUriString)
                    activeXray.decrementAndGet()

                    val target = inputTargetLive.text.toString().toIntOrNull() ?: 10
                    if (foundIps.size >= target) {
                        appendLog("🎯 TARGET REACHED!")
                        withContext(Dispatchers.Main) { stopScan() }
                    }
                }
            }
        }
    }

    private fun loadIpsFromAssets(): List<String> {
        return try { assets.open("ipv4.txt").bufferedReader().readLines() } catch (e: Exception) { emptyList() }
    }

    private fun generateRandomIp(subnets: List<String>): String {
        val parts = subnets.random().split("/")[0].split(".")
        return if (parts.size == 4) "${parts[0]}.${parts[1]}.${parts[2]}.${(1..254).random()}" else "104.16.${(0..255).random()}.${(1..254).random()}"
    }

    private suspend fun checkTcp(ip: String): Boolean = withContext(scanDispatcher) {
        return@withContext try { Socket().apply { connect(InetSocketAddress(ip, 443), 800); close() }; true } catch (e: Exception) { false }
    }

    private suspend fun checkTlsAndSpeed(ip: String): Boolean = withContext(scanDispatcher) {
        return@withContext try {
            val customDns = object : Dns { override fun lookup(hostname: String) = listOf(InetAddress.getByName(ip)) }

            // Memory Leak Fix: Share the base client pool!
            val client = baseClient.newBuilder()
                .dns(customDns)
                .connectTimeout(2, TimeUnit.SECONDS)
                .readTimeout(3, TimeUnit.SECONDS)
                .build()

            client.newCall(Request.Builder().url("https://speed.cloudflare.com/__down?bytes=500000").build()).execute().use { it.isSuccessful }
        } catch (e: Exception) { false }
    }

    private suspend fun verifyWithXray(ip: String, originalUri: String) = withContext(scanDispatcher) {
        var process: Process? = null
        var drainJob: Job? = null
        val localPort = (20000..50000).random()
        try {
            val configJson = buildRealVlessConfig(originalUri, ip, localPort)
            val configFile = File(cacheDir, "config_$localPort.json")
            configFile.writeText(configJson)

            if (isDebugEnabled) {
                appendLog("🔐 Xray Booting for $ip on port $localPort...")
                // Print the first 200 chars of JSON to verify parsing
                appendLog("📝 JSON Head: ${configJson.replace("\n", "").take(200)}...")
            }

            val xrayBinaryPath = applicationInfo.nativeLibraryDir + "/libxray.so"

            // Added .directory(cacheDir) to ensure Xray has workspace permissions
            process = ProcessBuilder(xrayBinaryPath, "run", "-c", configFile.absolutePath)
                .directory(cacheDir)
                .redirectErrorStream(true)
                .start()

            val processInput = process.inputStream.bufferedReader()
            drainJob = launch(Dispatchers.IO) {
                try {
                    while (isActive) {
                        val line = processInput.readLine() ?: break
                        // Filter out the annoying deprecation warnings to see the real errors
                        if (isDebugEnabled && !line.contains("deprecated")) {
                            appendLog("⚙️ XRAY ($ip): $line")
                        }
                    }
                } catch (e: Exception) {}
            }

            delay(1500)

            val proxy = Proxy(Proxy.Type.HTTP, InetSocketAddress("127.0.0.1", localPort))
            val client = baseClient.newBuilder()
                .proxy(proxy)
                .connectTimeout(5, TimeUnit.SECONDS)
                .readTimeout(5, TimeUnit.SECONDS)
                .build()

            val startTime = System.currentTimeMillis()
            client.newCall(request = Request.Builder().url("http://cp.cloudflare.com/generate_204").build()).execute().use { response ->
                if (response.isSuccessful || response.code == 204) {
                    val latency = System.currentTimeMillis() - startTime
                    val newUri = originalUri.replaceFirst(Regex("(vless://[^@]+@)([^:]+)(:\\d+)"), "$1$ip$3")
                    val displayUri = newUri.substringBefore("?") + "...#" + newUri.substringAfter("#", "Generated")

                    withContext(Dispatchers.Main) {
                        foundIps.add(ip)
                        generatedConfigs.add(newUri)
                        val target = inputTargetLive.text.toString().toIntOrNull() ?: 10
                        tvTargetProgress.text = "🎯 Target: ${foundIps.size} / $target"
                        tvConfigs.append("\n[${latency}ms] $displayUri")
                        appendLog("🎉 SUCCESS: $ip | ${latency}ms")
                    }
                } else {
                    if (isDebugEnabled) appendLog("❌ Bad HTTP Code from $ip: ${response.code}")
                }
            }
        } catch (e: java.net.ConnectException) {
            if (isDebugEnabled) appendLog("❌ Connect Refused on $ip (Xray crashed or port $localPort blocked)")
        } catch (e: java.net.SocketTimeoutException) {
            if (isDebugEnabled) appendLog("❌ Timeout on $ip (Cloudflare edge dropped the VLESS payload)")
        } catch (e: Exception) {
            if (isDebugEnabled) appendLog("❌ General Error on $ip: ${e.message}")
        } finally {
            drainJob?.cancel()
            try { process?.inputStream?.close() } catch (e: Exception) {}
            process?.destroy()
        }
    }

    private fun buildRealVlessConfig(uri: String, ip: String, localPort: Int): String {
        try {
            val mainPart = uri.substringAfter("vless://").substringBefore("#")
            val uuid = mainPart.substringBefore("@")
            val serverPortQuery = mainPart.substringAfter("@")
            val portQuery = serverPortQuery.substringAfter(":")
            val port = portQuery.substringBefore("?").toInt()
            val query = portQuery.substringAfter("?")

            val params = query.split("&").associate {
                val parts = it.split("=")
                // We keep the URLDecoder fix as it prevents path errors
                parts[0] to (if (parts.size > 1) URLDecoder.decode(parts[1], "UTF-8") else "")
            }

            val type = params["type"] ?: "tcp"
            val security = params["security"] ?: "none"
            var sni = params["sni"] ?: ""
            val path = params["path"] ?: "/"
            var host = params["host"] ?: ""
            val fp = params["fp"] ?: "chrome"

            // Keep the SNI Sync fix - this is what actually solved the 503s
            if (sni.isEmpty() && host.isNotEmpty()) sni = host
            if (host.isEmpty() && sni.isNotEmpty()) host = sni

            val alpnRaw = params["alpn"] ?: "http/1.1"
            val alpnList = alpnRaw.split(",").joinToString(", ") { "\"$it\"" }

            // Reverted to "allowInsecure": true to satisfy Android's lack of CA certs
            val tlsSettings = if (security == "tls") ",\"tlsSettings\": {\"allowInsecure\": true, \"serverName\": \"$sni\", \"fingerprint\": \"$fp\", \"alpn\": [$alpnList]}" else ""
            val hostHeader = if (host.isNotEmpty()) "\"Host\": \"$host\"" else ""
            val wsSettings = if (type == "ws") ",\"wsSettings\": {\"path\": \"$path\", \"headers\": {$hostHeader}}" else ""

            return """
            {
              "log": {"loglevel": "warning"},
              "inbounds": [{
                "port": $localPort,
                "listen": "127.0.0.1",
                "protocol": "mixed",
                "settings": {"allowTransparent": false}
              }],
              "outbounds": [{
                "protocol": "vless",
                "settings": {
                  "vnext": [{
                    "address": "$ip",
                    "port": $port,
                    "users": [{"id": "$uuid", "encryption": "none"}]
                  }]
                },
                "streamSettings": {
                  "network": "$type",
                  "security": "$security"
                  $tlsSettings
                  $wsSettings
                }
              }]
            }
            """.trimIndent()
        } catch (e: Exception) {
            appendLog("Error parsing URI: ${e.message}")
            return "{}"
        }
    }
}