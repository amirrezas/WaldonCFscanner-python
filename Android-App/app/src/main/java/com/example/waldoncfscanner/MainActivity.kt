package com.example.waldoncfscanner

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.net.Uri
import android.os.Bundle
import android.view.Gravity
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
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.Proxy
import java.net.Socket
import java.net.URLDecoder
import java.net.URLEncoder
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

data class IpCandidate(val ip: String, var tcpPing: Long = 0)
data class ScanResult(val ip: String, val tcpPing: Long, val xrayPing: Long, val speedKbps: Long, val configUri: String)

class MainActivity : AppCompatActivity() {

    private lateinit var pageSetup: LinearLayout
    private lateinit var pageScanner: LinearLayout
    private lateinit var pageResults: LinearLayout

    private lateinit var inputUri: EditText
    private lateinit var inputTargetSetup: EditText
    private lateinit var sliderPowerSetup: Slider
    private lateinit var tvPowerLabelSetup: TextView
    private lateinit var switchDebug: SwitchMaterial

    private lateinit var tvActiveUri: TextView
    private lateinit var pbTcp: LinearProgressIndicator
    private lateinit var pbTls: LinearProgressIndicator
    private lateinit var pbSpeed: LinearProgressIndicator
    private lateinit var pbXray: LinearProgressIndicator
    private lateinit var tvTargetProgress: TextView
    private lateinit var btnPauseResume: Button
    private lateinit var logScrollView: ScrollView
    private lateinit var tvLogs: TextView

    private lateinit var tvBestIpResult: TextView
    private lateinit var tableResults: TableLayout
    private lateinit var btnConnectBest: Button
    private lateinit var btnExportAll: Button
    private lateinit var btnExportIps: Button

    private val baseClient = OkHttpClient.Builder().connectionPool(ConnectionPool(100, 5, TimeUnit.MINUTES)).build()
    private val scanDispatcher = Executors.newFixedThreadPool(150).asCoroutineDispatcher()
    private var scannerScope: CoroutineScope? = null

    private var isScanning = false
    private var isPaused = false
    private var isDebugEnabled = false
    private var activeUriString = ""
    private val scanResultsList = mutableListOf<ScanResult>()

    private val cumulativeTcp = AtomicInteger(0)
    private val cumulativeTls = AtomicInteger(0)
    private val cumulativeSpeed = AtomicInteger(0)
    private val cumulativeXray = AtomicInteger(0)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        pageSetup = findViewById(R.id.pageSetup)
        pageScanner = findViewById(R.id.pageScanner)
        pageResults = findViewById(R.id.pageResults)

        inputUri = findViewById(R.id.inputUri)
        inputTargetSetup = findViewById(R.id.inputTargetSetup)
        sliderPowerSetup = findViewById(R.id.sliderPowerSetup)
        tvPowerLabelSetup = findViewById(R.id.tvPowerLabelSetup)
        switchDebug = findViewById(R.id.switchDebug)

        sliderPowerSetup.addOnChangeListener { _, value, _ -> tvPowerLabelSetup.text = "ENGINE POWER: ${value.toInt()}%" }

        tvActiveUri = findViewById(R.id.tvActiveUri)
        pbTcp = findViewById(R.id.pbTcp)
        pbTls = findViewById(R.id.pbTls)
        pbSpeed = findViewById(R.id.pbSpeed)
        pbXray = findViewById(R.id.pbXray)
        tvTargetProgress = findViewById(R.id.tvTargetProgress)
        btnPauseResume = findViewById(R.id.btnPauseResume)
        logScrollView = findViewById(R.id.logScrollView)
        tvLogs = findViewById(R.id.tvLogs)

        tvBestIpResult = findViewById(R.id.tvBestIpResult)
        tableResults = findViewById(R.id.tableResults)
        btnConnectBest = findViewById(R.id.btnConnectBest)
        btnExportAll = findViewById(R.id.btnExportAll)
        btnExportIps = findViewById(R.id.btnExportIps)

        findViewById<Button>(R.id.btnPaste).setOnClickListener { pasteFromClipboard() }
        findViewById<Button>(R.id.btnStartMain).setOnClickListener { initiateScan() }
        findViewById<Button>(R.id.btnGithub).setOnClickListener { startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("https://github.com/amirrezas/WaldonCFscanner"))) }

        findViewById<Button>(R.id.btnBackToSetup).setOnClickListener { resetToSetup() }
        btnPauseResume.setOnClickListener { togglePause() }
        findViewById<Button>(R.id.btnStopLive).setOnClickListener { stopScanAndShowResults() }
        findViewById<Button>(R.id.btnCopyLogs).setOnClickListener { copyToClipboard("Logs", tvLogs.text.toString()) }

        findViewById<Button>(R.id.btnBackToHome).setOnClickListener {
            pageResults.visibility = View.GONE
            pageSetup.visibility = View.VISIBLE
        }

        btnConnectBest.setOnClickListener {
            scanResultsList.minByOrNull { it.xrayPing }?.let { best -> launchVpnApp(best.configUri, "Best Config") }
        }

        btnExportAll.setOnClickListener {
            if (scanResultsList.isNotEmpty()) {
                val allConfigs = scanResultsList.joinToString("\n\n") { it.configUri }
                launchVpnApp(allConfigs, "All Verified Configs")
            }
        }

        btnExportIps.setOnClickListener {
            if (scanResultsList.isNotEmpty()) {
                val ipsOnly = scanResultsList.joinToString("\n") { it.ip }
                copyToClipboard("Verified IPs", ipsOnly)
            }
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        scanDispatcher.close()
    }

    private fun resetToSetup() {
        isScanning = false
        isPaused = false
        scannerScope?.cancel()
        pageScanner.visibility = View.GONE
        pageSetup.visibility = View.VISIBLE
    }

    private fun appendLog(msg: String) {
        runOnUiThread {
            val currentText = tvLogs.text.toString()
            if (currentText.length > 8000) tvLogs.text = currentText.substring(currentText.length - 4000)
            tvLogs.append("$msg\n")
            logScrollView.post { logScrollView.fullScroll(ScrollView.FOCUS_DOWN) }
        }
    }

    private fun copyToClipboard(label: String, text: String) {
        val clipboard = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        val clip = ClipData.newPlainText(label, text)
        clipboard.setPrimaryClip(clip)
        Toast.makeText(this, "$label copied to clipboard!", Toast.LENGTH_SHORT).show()
    }

    private fun pasteFromClipboard() {
        val clipboard = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        val item = clipboard.primaryClip?.getItemAt(0)?.text?.toString()
        if (item != null && (item.startsWith("vless://") || item.startsWith("trojan://"))) {
            inputUri.setText(item)
            Toast.makeText(this, "URI Pasted!", Toast.LENGTH_SHORT).show()
        }
    }

    private fun launchVpnApp(configsToCopy: String, label: String) {
        copyToClipboard(label, configsToCopy)
        try {
            val fallbackIntent = packageManager.getLaunchIntentForPackage("com.v2ray.ang")
                ?: packageManager.getLaunchIntentForPackage("moe.nb4a")

            if (fallbackIntent != null) {
                startActivity(fallbackIntent)
            } else {
                Toast.makeText(this, "Configs copied! Open your VPN app to paste.", Toast.LENGTH_LONG).show()
            }
        } catch (e: Exception) {
            Toast.makeText(this, "Configs copied! Open your VPN app to paste.", Toast.LENGTH_LONG).show()
        }
    }

    private fun initiateScan() {
        activeUriString = inputUri.text.toString().trim()
        if (!activeUriString.startsWith("vless://") && !activeUriString.startsWith("trojan://")) {
            Toast.makeText(this, "Valid VLESS or Trojan URI required.", Toast.LENGTH_SHORT).show()
            return
        }
        pageSetup.visibility = View.GONE
        pageScanner.visibility = View.VISIBLE
        tvActiveUri.text = activeUriString
        startEngine()
    }

    private fun startEngine() {
        isScanning = true
        isPaused = false
        isDebugEnabled = switchDebug.isChecked
        btnPauseResume.text = "⏸ PAUSE"
        btnPauseResume.setBackgroundColor(android.graphics.Color.parseColor("#FF9800"))

        scanResultsList.clear()
        tvLogs.text = "🚀 Starting Engine...\n"

        cumulativeTcp.set(0)
        cumulativeTls.set(0)
        cumulativeSpeed.set(0)
        cumulativeXray.set(0)

        val target = inputTargetSetup.text.toString().toIntOrNull() ?: 10
        val power = sliderPowerSetup.value.toInt()

        pbTcp.max = target * 10
        pbTls.max = target * 5
        pbSpeed.max = target * 2
        pbXray.max = target

        scannerScope?.cancel()
        scannerScope = CoroutineScope(scanDispatcher + SupervisorJob())

        scannerScope?.launch {
            try {
                runScannerPipeline(power, target)
            } catch (e: CancellationException) {
                appendLog("⏹️ Workers terminated safely.")
            } catch (e: Exception) {
                appendLog("❌ Fatal Error: ${e.message}")
            }
        }

        scannerScope?.launch(Dispatchers.Main) {
            while (isActive && isScanning) {
                pbTcp.setProgressCompat(cumulativeTcp.get().coerceAtMost(pbTcp.max), true)
                pbTls.setProgressCompat(cumulativeTls.get().coerceAtMost(pbTls.max), true)
                pbSpeed.setProgressCompat(cumulativeSpeed.get().coerceAtMost(pbSpeed.max), true)
                pbXray.setProgressCompat(cumulativeXray.get().coerceAtMost(pbXray.max), true)
                delay(200)
            }
        }
    }

    private fun togglePause() {
        if (!isScanning) return
        isPaused = !isPaused
        if (isPaused) {
            btnPauseResume.text = "▶ RESUME"
            btnPauseResume.setBackgroundColor(android.graphics.Color.parseColor("#4CAF50"))
            appendLog("⏸️ Engine Paused.")
        } else {
            btnPauseResume.text = "⏸ PAUSE"
            btnPauseResume.setBackgroundColor(android.graphics.Color.parseColor("#FF9800"))
            appendLog("▶️ Engine Resumed.")
        }
    }

    private fun stopScanAndShowResults() {
        isScanning = false
        isPaused = false
        scannerScope?.cancel()

        runOnUiThread {
            pbTcp.setProgressCompat(pbTcp.max, true)
            pbTls.setProgressCompat(pbTls.max, true)
            pbSpeed.setProgressCompat(pbSpeed.max, true)
            pbXray.setProgressCompat(pbXray.max, true)
        }

        buildResultsPage()
        pageScanner.visibility = View.GONE
        pageResults.visibility = View.VISIBLE
    }

    private fun buildResultsPage() {
        if (scanResultsList.isEmpty()) {
            tvBestIpResult.text = "No IPs Found. Try again."
            btnConnectBest.isEnabled = false
            btnExportAll.isEnabled = false
            btnExportIps.isEnabled = false
            tableResults.removeAllViews()
            return
        }

        btnConnectBest.isEnabled = true
        btnExportAll.isEnabled = true
        btnExportIps.isEnabled = true

        scanResultsList.sortByDescending { it.speedKbps }

        val best = scanResultsList.first()
        tvBestIpResult.text = "${best.ip}\n⚡ ${best.speedKbps} KB/s | ${best.xrayPing}ms TTFB"

        tableResults.removeAllViews()

        val headerRow = TableRow(this).apply { setPadding(0, 0, 0, 16) }
        headerRow.addView(createTextView("IP", true, 3.5f, Gravity.START or Gravity.CENTER_VERTICAL))
        headerRow.addView(createTextView("TCP", true, 1.5f, Gravity.CENTER))
        headerRow.addView(createTextView("LAT", true, 1.5f, Gravity.CENTER))
        headerRow.addView(createTextView("SPEED", true, 2.0f, Gravity.CENTER))
        headerRow.addView(createTextView("ACT", true, 1.5f, Gravity.CENTER))
        tableResults.addView(headerRow)

        for (result in scanResultsList) {
            val row = TableRow(this).apply {
                setPadding(0, 12, 0, 12)
                gravity = Gravity.CENTER_VERTICAL
            }

            row.addView(createTextView(result.ip, false, 3.5f, Gravity.START or Gravity.CENTER_VERTICAL))
            row.addView(createTextView("${result.tcpPing}ms", false, 1.5f, Gravity.CENTER))

            val latencyText = createTextView("${result.xrayPing}ms", false, 1.5f, Gravity.CENTER)
            latencyText.setTextColor(if (result.xrayPing < 1000) Color.parseColor("#00E676") else Color.parseColor("#FF9800"))
            row.addView(latencyText)

            val speedText = createTextView("${result.speedKbps} KB/s", false, 2.0f, Gravity.CENTER)
            speedText.setTextColor(if (result.speedKbps > 150) Color.parseColor("#00BCD4") else Color.parseColor("#FF9800"))
            row.addView(speedText)

            val copyBtn = Button(this, null, android.R.attr.borderlessButtonStyle).apply {
                text = "COPY"
                textSize = 9f
                setTextColor(Color.parseColor("#2196F3"))
                setPadding(0, 0, 0, 0)
                layoutParams = TableRow.LayoutParams(0, TableRow.LayoutParams.WRAP_CONTENT, 1.5f)
                gravity = Gravity.CENTER
                setOnClickListener { copyToClipboard("Config", result.configUri) }
            }
            row.addView(copyBtn)

            tableResults.addView(row)

            val divider = View(this).apply {
                layoutParams = TableLayout.LayoutParams(TableLayout.LayoutParams.MATCH_PARENT, 1)
                setBackgroundColor(Color.parseColor("#333333"))
            }
            tableResults.addView(divider)
        }
    }

    private fun createTextView(text: String, isHeader: Boolean, weight: Float, align: Int): TextView {
        return TextView(this).apply {
            this.text = text
            this.setTextColor(if (isHeader) Color.parseColor("#888888") else Color.WHITE)
            this.textSize = if (isHeader) 10f else 11f
            this.gravity = align
            this.setPadding(8, 0, 8, 0)
            this.maxLines = 1
            this.ellipsize = android.text.TextUtils.TruncateAt.END
            if (isHeader) this.setTypeface(null, android.graphics.Typeface.BOLD)
            this.layoutParams = TableRow.LayoutParams(0, TableRow.LayoutParams.WRAP_CONTENT, weight)
        }
    }

    private suspend fun runScannerPipeline(powerPercent: Int, target: Int) = coroutineScope {
        val ips = loadIpsFromAssets()
        if (ips.isEmpty()) return@coroutineScope

        val activeSockets = (150 * (powerPercent / 100.0)).toInt().coerceAtLeast(5)
        val numTcpWorkers = (activeSockets * 0.70).toInt().coerceAtLeast(2)
        val numTlsWorkers = (activeSockets * 0.20).toInt().coerceAtLeast(1)
        val numSpeedWorkers = (activeSockets * 0.10).toInt().coerceAtLeast(1)
        val numXrayWorkers = 5

        withContext(Dispatchers.Main) { appendLog("⚙️ Armed workers: TCP:$numTcpWorkers TLS:$numTlsWorkers SPD:$numSpeedWorkers XRY:$numXrayWorkers") }

        val rawQueue = Channel<IpCandidate>(numTcpWorkers * 2)
        val tcpQueue = Channel<IpCandidate>(numTlsWorkers * 2)
        val xrayQueue = Channel<IpCandidate>(numXrayWorkers * 2)

        launch {
            while (isActive && isScanning) {
                if (!isPaused) rawQueue.send(IpCandidate(generateRandomIp(ips)))
                delay(20)
            }
        }

        repeat(numTcpWorkers) {
            launch {
                for (candidate in rawQueue) {
                    if (!isActive) break
                    while (isPaused) delay(200)
                    if (checkTcp(candidate)) {
                        cumulativeTcp.incrementAndGet()
                        tcpQueue.send(candidate)
                    }
                }
            }
        }

        repeat(numTlsWorkers + numSpeedWorkers) {
            launch {
                for (candidate in tcpQueue) {
                    if (!isActive) break
                    while (isPaused) delay(200)
                    if (checkTlsAndSpeed(candidate)) {
                        cumulativeTls.incrementAndGet()
                        cumulativeSpeed.incrementAndGet()
                        xrayQueue.send(candidate)
                    }
                }
            }
        }

        repeat(numXrayWorkers) {
            launch {
                for (candidate in xrayQueue) {
                    if (!isActive) break
                    while (isPaused) delay(200)
                    verifyWithXray(candidate, activeUriString, target)
                }
            }
        }
    }

    private fun loadIpsFromAssets(): List<String> { return try { assets.open("ipv4.txt").bufferedReader().readLines() } catch (e: Exception) { emptyList() } }
    private fun generateRandomIp(subnets: List<String>): String { val parts = subnets.random().split("/")[0].split("."); return if (parts.size == 4) "${parts[0]}.${parts[1]}.${parts[2]}.${(1..254).random()}" else "104.16.${(0..255).random()}.${(1..254).random()}" }

    private suspend fun checkTcp(candidate: IpCandidate): Boolean = withContext(scanDispatcher) {
        return@withContext try {
            val start = System.currentTimeMillis()
            Socket().apply { connect(InetSocketAddress(candidate.ip, 443), 800); close() }
            candidate.tcpPing = System.currentTimeMillis() - start
            true
        } catch (e: Exception) { false }
    }

    private suspend fun checkTlsAndSpeed(candidate: IpCandidate): Boolean = withContext(scanDispatcher) {
        return@withContext try {
            val customDns = object : Dns { override fun lookup(hostname: String) = listOf(InetAddress.getByName(candidate.ip)) }
            val client = baseClient.newBuilder().dns(customDns).connectTimeout(2, TimeUnit.SECONDS).readTimeout(3, TimeUnit.SECONDS).build()
            client.newCall(Request.Builder().url("https://speed.cloudflare.com/__down?bytes=10000").build()).execute().use { it.isSuccessful }
        } catch (e: Exception) { false }
    }

    private suspend fun verifyWithXray(candidate: IpCandidate, originalUri: String, target: Int) = withContext(scanDispatcher) {
        var process: Process? = null
        var drainJob: Job? = null
        val localPort = (20000..50000).random()
        try {
            val configJson = buildRealConfig(originalUri, candidate.ip, localPort)
            val configFile = File(cacheDir, "config_$localPort.json")
            configFile.writeText(configJson)

            val xrayBinaryPath = applicationInfo.nativeLibraryDir + "/libxray.so"
            process = ProcessBuilder(xrayBinaryPath, "run", "-c", configFile.absolutePath).directory(cacheDir).redirectErrorStream(true).start()

            drainJob = launch(Dispatchers.IO) {
                try {
                    val processInput = process.inputStream.bufferedReader()
                    while (isActive) {
                        val line = processInput.readLine() ?: break
                        if (isDebugEnabled && !line.contains("deprecated")) appendLog("⚙️ XRAY (${candidate.ip}): $line")
                    }
                } catch (e: Exception) {}
            }

            delay(1500)

            val proxy = Proxy(Proxy.Type.HTTP, InetSocketAddress("127.0.0.1", localPort))
            val client = baseClient.newBuilder()
                .proxy(proxy)
                .connectTimeout(10, TimeUnit.SECONDS)
                .readTimeout(10, TimeUnit.SECONDS)
                .build()

            // INCREASED PAYLOAD: Requesting 500KB to get a true bandwidth reading
            val request = Request.Builder().url("https://speed.cloudflare.com/__down?bytes=500000").build()

            val startTime = System.currentTimeMillis()
            client.newCall(request).execute().use { response ->
                if (response.isSuccessful) {
                    // TTFB (Time To First Byte): This is the true connection latency
                    val ttfb = System.currentTimeMillis() - startTime

                    val body = response.body
                    if (body != null) {
                        val inputStream = body.byteStream()
                        val buffer = ByteArray(8192) // 8KB Streaming Buffer
                        var totalRead = 0L
                        val downloadStartTime = System.currentTimeMillis()

                        // Stream the file chunk by chunk to calculate pure download bandwidth
                        while (isActive) {
                            val read = inputStream.read(buffer)
                            if (read == -1) break
                            totalRead += read
                        }

                        val downloadTime = System.currentTimeMillis() - downloadStartTime

                        // Ensure we actually downloaded a large file, proving it wasn't intercepted by Cloudflare
                        if (totalRead >= 100000) {
                            cumulativeXray.incrementAndGet()

                            // True Speed Calculation (Bytes -> KB) / (Millis -> Sec)
                            val speedKbps = if (downloadTime > 0) {
                                ((totalRead / 1024.0) / (downloadTime / 1000.0)).toLong()
                            } else {
                                0L
                            }

                            val uri = java.net.URI(originalUri)
                            val scheme = uri.scheme?.lowercase() ?: "vless"
                            val uuid = uri.userInfo ?: ""
                            val portStr = if (uri.port != -1) uri.port.toString() else "443"
                            val originalAddress = uri.host ?: ""

                            val rawQuery = uri.rawQuery ?: ""
                            val params = mutableMapOf<String, String>()
                            rawQuery.split("&").forEach {
                                val parts = it.split("=")
                                if (parts.size >= 2) params[parts[0]] = URLDecoder.decode(parts[1], "UTF-8")
                                else if (parts.isNotEmpty() && parts[0].isNotEmpty()) params[parts[0]] = ""
                            }

                            if (params["sni"].isNullOrEmpty()) params["sni"] = originalAddress
                            if (params["host"].isNullOrEmpty()) params["host"] = params["sni"]!!
                            if (params["fp"].isNullOrEmpty()) params["fp"] = "chrome"

                            val newQuery = params.entries.joinToString("&") { "${it.key}=${URLEncoder.encode(it.value, "UTF-8")}" }
                            val title = if (originalUri.contains("#")) originalUri.substringAfter("#") else "Verified"
                            val formattedIp = if (candidate.ip.contains(":")) "[${candidate.ip}]" else candidate.ip

                            val newUri = "$scheme://$uuid@$formattedIp:$portStr?$newQuery#$title"

                            withContext(Dispatchers.Main) {
                                scanResultsList.add(ScanResult(candidate.ip, candidate.tcpPing, ttfb, speedKbps, newUri))
                                appendLog("🎉 SUCCESS: ${candidate.ip} | ${speedKbps}KB/s | ${ttfb}ms TTFB")
                                tvTargetProgress.text = "🎯 Target: ${scanResultsList.size} / $target"

                                if (scanResultsList.size >= target) {
                                    appendLog("🎯 TARGET REACHED!")
                                    stopScanAndShowResults()
                                }
                            }
                        } else {
                            if (isDebugEnabled) appendLog("❌ Payload too small from ${candidate.ip} (Blocked by Edge)")
                        }
                    }
                } else {
                    if (isDebugEnabled) appendLog("❌ Bad HTTP ${response.code} from ${candidate.ip}")
                }
            }
        } catch (e: Exception) {
            if (isDebugEnabled) appendLog("❌ Error on ${candidate.ip}: ${e.javaClass.simpleName}")
        } finally {
            drainJob?.cancel()
            try { process?.inputStream?.close() } catch (e: Exception) {}
            process?.destroy()
        }
    }

    private fun buildRealConfig(uriString: String, ip: String, localPort: Int): String {
        try {
            val uri = java.net.URI(uriString)
            val scheme = uri.scheme?.lowercase() ?: return "{}"
            val uuid = uri.userInfo ?: ""
            var port = uri.port
            if (port == -1) port = 443

            val rawQuery = uri.rawQuery ?: ""
            val params = mutableMapOf<String, String>()
            rawQuery.split("&").forEach {
                val parts = it.split("=")
                if (parts.size >= 2) {
                    params[parts[0]] = URLDecoder.decode(parts[1], "UTF-8")
                } else if (parts.isNotEmpty() && parts[0].isNotEmpty()) {
                    params[parts[0]] = ""
                }
            }

            val type = params["type"] ?: "tcp"
            val security = params["security"] ?: "none"
            var sni = params["sni"] ?: ""
            var host = params["host"] ?: ""
            val path = params["path"] ?: "/"
            val fp = params["fp"] ?: "chrome"
            val mode = params["mode"] ?: "auto"
            val serviceName = params["serviceName"] ?: path

            if (sni.isEmpty()) sni = host
            if (sni.isEmpty()) sni = uri.host ?: ""
            if (host.isEmpty()) host = sni

            val defaultAlpn = if (type == "ws") "http/1.1" else "h2,http/1.1"
            val alpnListStr = params["alpn"] ?: defaultAlpn
            val alpnList = alpnListStr.split(",").map { it.trim() }

            val root = JSONObject()
            val log = JSONObject().put("loglevel", "warning")
            root.put("log", log)

            val inbound = JSONObject()
            inbound.put("port", localPort)
            inbound.put("listen", "127.0.0.1")
            inbound.put("protocol", "mixed")
            inbound.put("settings", JSONObject().put("allowTransparent", false))
            root.put("inbounds", JSONArray().put(inbound))

            val outbound = JSONObject()
            outbound.put("protocol", scheme)

            val settings = JSONObject()
            if (scheme == "trojan") {
                val server = JSONObject()
                server.put("address", ip)
                server.put("port", port)
                server.put("password", uuid)
                settings.put("servers", JSONArray().put(server))
            } else {
                val vnext = JSONObject()
                vnext.put("address", ip)
                vnext.put("port", port)
                val user = JSONObject()
                user.put("id", uuid)
                user.put("encryption", params["encryption"] ?: "none")
                vnext.put("users", JSONArray().put(user))
                settings.put("vnext", JSONArray().put(vnext))
            }
            outbound.put("settings", settings)

            val streamSettings = JSONObject()
            streamSettings.put("network", type)
            streamSettings.put("security", security)

            if (security == "tls") {
                val tlsSettings = JSONObject()
                tlsSettings.put("allowInsecure", true)
                tlsSettings.put("serverName", sni)
                tlsSettings.put("fingerprint", fp)
                val alpnArray = JSONArray()
                alpnList.forEach { alpnArray.put(it) }
                tlsSettings.put("alpn", alpnArray)
                streamSettings.put("tlsSettings", tlsSettings)
            }

            when (type) {
                "ws" -> {
                    val wsSettings = JSONObject()
                    wsSettings.put("path", path)
                    val headers = JSONObject()
                    if (host.isNotEmpty()) headers.put("Host", host)
                    wsSettings.put("headers", headers)
                    streamSettings.put("wsSettings", wsSettings)
                }
                "xhttp" -> {
                    val xhttpSettings = JSONObject()
                    xhttpSettings.put("path", path)
                    if (host.isNotEmpty()) xhttpSettings.put("host", host)
                    xhttpSettings.put("mode", mode)
                    streamSettings.put("xhttpSettings", xhttpSettings)
                }
                "grpc" -> {
                    val grpcSettings = JSONObject()
                    grpcSettings.put("serviceName", serviceName)
                    if (mode == "multi") grpcSettings.put("multiMode", true)
                    streamSettings.put("grpcSettings", grpcSettings)
                }
                "tcp" -> {
                    if (params["headerType"] == "http") {
                        val tcpSettings = JSONObject()
                        val header = JSONObject()
                        header.put("type", "http")
                        val request = JSONObject()
                        val reqHeaders = JSONObject()
                        if (host.isNotEmpty()) reqHeaders.put("Host", JSONArray().put(host))
                        request.put("headers", reqHeaders)
                        request.put("path", JSONArray().put(path))
                        header.put("request", request)
                        tcpSettings.put("header", header)
                        streamSettings.put("tcpSettings", tcpSettings)
                    }
                }
            }

            outbound.put("streamSettings", streamSettings)
            root.put("outbounds", JSONArray().put(outbound))

            return root.toString()
        } catch (e: Exception) {
            return "{}"
        }
    }
}