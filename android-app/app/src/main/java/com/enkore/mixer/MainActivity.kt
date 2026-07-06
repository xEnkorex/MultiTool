package com.enkore.mixer

import android.annotation.SuppressLint
import android.app.Activity
import android.app.AlertDialog
import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.os.Bundle
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.EditText
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import com.google.zxing.integration.android.IntentIntegrator

/**
 * Envoltorio nativo mínimo: un WebView a pantalla completa apuntando al
 * servidor de Enkore Mixer en la LAN. No es un TWA (eso necesitaría HTTPS
 * + Digital Asset Links, que no tenemos en una red doméstica) — acá el
 * WebView carga HTTP plano sin problema (`usesCleartextTraffic` en el
 * manifest) y este código controla el modo inmersivo directo, sin
 * depender de la Fullscreen API del navegador.
 *
 * La dirección del servidor (IP:puerto de la PC) se guarda en
 * SharedPreferences, y se puede cargar de dos formas: escribiéndola a
 * mano, o escaneando el mismo QR que ya muestra el menú "Info / código
 * QR" del ícono de la bandeja en Windows (codifica la URL completa).
 * Mantené presionada la pantalla para volver a abrir este diálogo si la
 * IP de la PC cambia.
 */
class MainActivity : Activity() {

    private lateinit var webView: WebView
    private lateinit var prefs: SharedPreferences

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

        webView = WebView(this)
        setContentView(webView)

        webView.settings.javaScriptEnabled = true
        webView.settings.domStorageEnabled = true
        webView.settings.cacheMode = WebSettings.LOAD_NO_CACHE
        webView.webViewClient = WebViewClient()
        webView.setOnLongClickListener {
            showServerDialog()
            true
        }

        enableImmersiveMode()

        val savedUrl = prefs.getString(KEY_SERVER_URL, null)
        if (savedUrl.isNullOrBlank()) {
            showServerDialog()
        } else {
            webView.loadUrl(savedUrl)
        }
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) enableImmersiveMode()
    }

    private fun enableImmersiveMode() {
        WindowCompat.setDecorFitsSystemWindows(window, false)
        val controller = WindowInsetsControllerCompat(window, window.decorView)
        controller.hide(WindowInsetsCompat.Type.systemBars())
        controller.systemBarsBehavior =
            WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
    }

    private fun saveAndLoad(rawAddress: String) {
        var address = rawAddress.trim()
        if (address.isEmpty()) return
        if (!address.startsWith("http://") && !address.startsWith("https://")) {
            address = "http://$address"
        }
        prefs.edit().putString(KEY_SERVER_URL, address).apply()
        webView.loadUrl(address)
    }

    private fun launchQrScan() {
        val integrator = IntentIntegrator(this)
        integrator.setDesiredBarcodeFormats(IntentIntegrator.QR_CODE)
        integrator.setPrompt("Escaneá el QR del menú \"Info / código QR\" del tray en la PC")
        integrator.setBeepEnabled(false)
        integrator.setOrientationLocked(true)
        integrator.initiateScan()
    }

    @Suppress("DEPRECATION")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        val result = IntentIntegrator.parseActivityResult(requestCode, resultCode, data)
        if (result?.contents != null) {
            saveAndLoad(result.contents)
        } else {
            super.onActivityResult(requestCode, resultCode, data)
        }
    }

    private fun showServerDialog() {
        val currentUrl = prefs.getString(KEY_SERVER_URL, null)
        val input = EditText(this)
        input.hint = "192.168.1.83:8000"
        currentUrl?.let { input.setText(it.removePrefix("http://").removePrefix("https://")) }

        val builder = AlertDialog.Builder(this)
            .setTitle("Dirección del servidor")
            .setMessage(
                "Escribila a mano, o escaneá el QR del menú \"Info / código QR\" " +
                    "del ícono de la bandeja en Windows.\n\n" +
                    "(Mantené presionada la pantalla para volver a abrir esto.)"
            )
            .setView(input)
            .setPositiveButton("Conectar") { _, _ -> saveAndLoad(input.text.toString()) }
            .setNeutralButton("Escanear QR") { _, _ -> launchQrScan() }

        if (!currentUrl.isNullOrBlank()) {
            builder.setNegativeButton("Cancelar", null)
        }
        builder.show()
    }

    @Suppress("DEPRECATION")
    override fun onBackPressed() {
        if (webView.canGoBack()) {
            webView.goBack()
        } else {
            super.onBackPressed()
        }
    }

    companion object {
        private const val PREFS_NAME = "enkore_mixer"
        private const val KEY_SERVER_URL = "server_url"
    }
}
