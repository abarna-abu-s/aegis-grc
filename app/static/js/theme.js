try { var t = localStorage.getItem('aegis_theme'); if (t) document.documentElement.dataset.theme = t; } catch (e) { /* storage unavailable */ }
