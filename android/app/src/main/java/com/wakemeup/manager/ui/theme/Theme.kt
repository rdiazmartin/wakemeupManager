package com.wakemeup.manager.ui.theme

import android.os.Build
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext

/**
 * Tema oscuro por defecto (UX-DR1): Material 3 + Dynamic Color (Material You)
 * con fallback a la paleta grafito de DESIGN.md §Colors.
 * Sin degradados ni brillos: superficies de tono, estados con punto + texto.
 */
@Composable
fun WakemeupTheme(content: @Composable () -> Unit) {
    val context = LocalContext.current
    val graphiteColors = darkColorScheme(
        primary = AccentMint,
        onPrimary = Color(0xFF00201A),
        secondary = AccentMint,
        onSecondary = Color(0xFF00201A),
        background = GraphiteBase,
        onBackground = InkPrimary,
        surface = GraphiteBase,
        onSurface = InkPrimary,
        surfaceVariant = GraphiteRaised,
        onSurfaceVariant = InkSecondary,
        surfaceContainer = GraphiteRaised,
        surfaceContainerHigh = GraphiteRaised,
        surfaceContainerHighest = Color(0xFF23272C),
        surfaceContainerLow = Color(0xFF15181B),
        surfaceContainerLowest = GraphiteSunken,
        outline = InkDisabled,
        outlineVariant = Color(0xFF3A4047),
        error = DangerRed,
        onError = Color(0xFF2B0A0A),
    )
    // Dynamic Color (Material You) solo en Android 12+ (SDK 31); fuera de ahí degrada al grafito.
    val scheme = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
        try {
            dynamicDarkColorScheme(context)
        } catch (_: Throwable) {
            graphiteColors
        }
    } else {
        graphiteColors
    }

    MaterialTheme(
        colorScheme = scheme,
        typography = AppTypography,
        content = content,
    )
}
