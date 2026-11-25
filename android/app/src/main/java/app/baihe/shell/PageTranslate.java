package app.baihe.shell;

/**
 * Page scripts for Grab's optional "Translate this page": Google's free website-translate widget
 * runs inside the page, so the address, cookies and logins stay the site's own and the ad blocking
 * in AdHosts still applies. Translating rewrites the page's text, so a grab turns it off first.
 */
final class PageTranslate {
    private PageTranslate() {}

    // The widget keeps its choice in a googtrans cookie on the site; clearing it on the way out
    // stops the next page load translating by itself after the person switched it off.
    private static final String CLEAR_COOKIE =
            "var h=location.hostname,p=h.split('.');"
            + "['',h,'.'+h,p.length>2?'.'+p.slice(-2).join('.'):''].forEach(function(d){"
            + "document.cookie='googtrans=;expires=Thu, 01 Jan 1970 00:00:00 GMT;path=/'"
            + "+(d?';domain='+d:'');});";

    private static final String PICK =
            "function pick(v){var c=document.querySelector('select.goog-te-combo');"
            + "if(!c)return false;c.value=v;c.dispatchEvent(new Event('change'));return true;}";

    /** Loads the widget on first use, then asks for English; safe to run again after each page load. */
    static final String ON = "(function(){" + PICK
            + "window.__baiheTranslate=true;"
            + "if(!document.getElementById('baihe-te-css')){var s=document.createElement('style');"
            + "s.id='baihe-te-css';s.textContent='.skiptranslate iframe,#goog-gt-tt{display:none!important}"
            + "body{top:0!important}';document.head.appendChild(s);}"
            + "function go(n){if(!window.__baiheTranslate)return;if(pick('en'))return;"
            + "if(n<40)setTimeout(function(){go(n+1)},250);}"
            + "if(document.querySelector('select.goog-te-combo')){go(0);return;}"
            + "window.__baiheTeInit=function(){var d=document.createElement('div');d.style.display='none';"
            + "document.body.appendChild(d);"
            + "new google.translate.TranslateElement({pageLanguage:'auto',includedLanguages:'en',"
            + "autoDisplay:false},d);go(0);};"
            + "if(window.google&&google.translate&&google.translate.TranslateElement){window.__baiheTeInit();return;}"
            + "var sc=document.createElement('script');"
            + "sc.src='https://translate.google.com/translate_a/element.js?cb=__baiheTeInit';"
            + "document.head.appendChild(sc);})();";

    /** Puts the original text back without reloading, so the page keeps its place. */
    static final String OFF = "(function(){" + PICK
            + "window.__baiheTranslate=false;pick('');" + CLEAR_COOKIE + "})();";
}
