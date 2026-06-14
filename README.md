
###***Test base for try difference models voice don't download it has many bug super annoying with torch***###

#first try with marvis tts due to annoying torch cuda fail for add it

if not work yet but it nealy work now with f5 tts and kokoro back to online mormally should
has problem with gui clone voice did not works as expect 

***fish speech hole/abandon state.***
***Didn't have Powerfully GPU to test and it not function yet***

```
LocalReader-Plus
├── README.md
├── CHANGELOG.md
└── dist/
    ├── main.py                  # App entry point (FastAPI + WebView)
    │
    ├── app/
    │   ├── server.py            # FastAPI initialization
    │   ├── state.py             # Global engine/status singleton
    │   ├── routers/             # API Controllers (TTS, Library, Export, etc.)
    │   ├── logic/               # Core logic (Normalize, Detector, Cache)
    │   ├── models/              # has models TTS engine inside 
    |   |__ locales/             # UI Translations (EN, ES, FR, ZH, JA)
    │   └── ui/
    │       ├── index.html       # Main SPA
    │       ├── css/style.css    # Premium styling
    │       └── js/modules/      # ES6 Logic modules
    │
    └── userdata/                # User settings and book database
    ```
