import json
import re
import base64

# --- HTML Template (Redesigned to match your Dashboard Theme) ---
HTML_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>H4R Test Engine</title>
<script src="https://cdn.tailwindcss.com"></script>
<link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
<!-- 1. ADDED OUTFIT FONT TO MATCH YOUR DESIGN -->
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700;800;900&display=swap" rel="stylesheet">

<!-- 2. MATHJAX CONFIGURATION FOR PROPER FORMULA RENDERING -->
<script>
    window.MathJax = {
        tex: {
            inlineMath: [['$', '$'], ['\\(', '\\)']],
            displayMath: [['$$', '$$'], ['\\[', '\\]']]
        },
        startup: {
            typeset: false // Hum isko manually trigger karenge jab question load hoga
        }
    };
</script>
<script id="MathJax-script" async src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script>

<style>
/* 3. UPDATED CSS VARIABLES TO MATCH YOUR EXPLORE/DASHBOARD PAGE */
:root {
    --bg-base: #f4f4f5; 
    --bg-surface: #ffffff; 
    --bg-surface-hover: #f4f4f5;
    --border-subtle: #e4e4e7; 
    --text-primary: #09090b; 
    --text-secondary: #71717a;
    --accent-main: #4f46e5; 
    --accent-glow: rgba(79, 70, 229, 0.15);
    --success: #10b981; 
    --warning: #f59e0b; 
    --danger: #ef4444;
}

.dark-mode {
    --bg-base: #000000; 
    --bg-surface: #111111; 
    --bg-surface-hover: #1f1f22;
    --border-subtle: #27272a; 
    --text-primary: #fafafa; 
    --text-secondary: #a1a1aa;
    --accent-main: #6366f1; 
    --accent-glow: rgba(99, 102, 241, 0.25);
}

* { box-sizing: border-box; font-family: 'Outfit', sans-serif; -webkit-tap-highlight-color: transparent; }
body { background-color: var(--bg-base); color: var(--text-primary); margin: 0; transition: background-color 0.4s ease; }

/* BENTO CARD DESIGN */
.bento-card { background: var(--bg-surface); border: 1px solid var(--border-subtle); border-radius: 24px; padding: 20px; box-shadow: 0 4px 20px rgba(0,0,0,0.02); transition: 0.3s; }
.bento-header { position: sticky; top: 0; z-index: 20; background: var(--bg-surface); border-bottom: 1px solid var(--border-subtle); padding: 16px; display: flex; justify-content: space-between; align-items: center; box-shadow: 0 4px 20px rgba(0,0,0,0.02); }

/* SCROLLBAR */
.question-content-area { overflow-x: auto; width: 100%; }
::-webkit-scrollbar { width: 4px; height: 4px; }
::-webkit-scrollbar-thumb { background: var(--border-subtle); border-radius: 4px; }

/* OPTIONS UI */
.option-row { background: var(--bg-base); border: 1px solid var(--border-subtle); border-radius: 16px; padding: 14px 16px; margin-bottom: 12px; display: flex; align-items: flex-start; cursor: pointer; transition: 0.2s cubic-bezier(0.4, 0, 0.2, 1); font-weight: 500; font-size: 15px; }
.option-row.selected { border-color: var(--accent-main); background: var(--accent-glow); }
.option-radio { -webkit-appearance: none; appearance: none; border: 2px solid var(--text-secondary); border-radius: 50%; width: 20px; height: 20px; min-width: 20px; cursor: pointer; transition: 0.2s; position: relative; margin-top: 2px; }
.option-row.selected .option-radio { border-color: var(--accent-main); background-color: var(--accent-main); }
.option-row.selected .option-radio::after { content: ''; display: block; width: 8px; height: 8px; background: white; border-radius: 50%; position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); }

/* BUTTONS */
.btn-primary { background: var(--accent-main); color: white; border: none; padding: 10px 16px; border-radius: 16px; font-weight: 700; cursor: pointer; transition: 0.2s; box-shadow: 0 4px 15px var(--accent-glow); display: inline-flex; align-items: center; gap: 8px; }
.btn-primary:active { transform: scale(0.95); }
.btn-danger { background: var(--danger); color: white; border: none; padding: 10px 16px; border-radius: 16px; font-weight: 700; cursor: pointer; transition: 0.2s; box-shadow: 0 4px 15px rgba(239, 68, 68, 0.2); }
.btn-danger:active { transform: scale(0.95); }
.btn-outline { background: transparent; border: 1px solid var(--border-subtle); color: var(--text-primary); padding: 10px 16px; border-radius: 16px; font-weight: 700; cursor: pointer; transition: 0.2s; }
.btn-outline:active { transform: scale(0.95); background: var(--bg-surface-hover); }

/* GRID NAV BUTTONS */
.question-nav-btn { width: 40px; height: 40px; border-radius: 12px; font-weight: 700; display: flex; align-items: center; justify-content: center; cursor: pointer; transition: 0.2s; border: 1px solid var(--border-subtle); background: var(--bg-base); color: var(--text-primary); }
.question-nav-btn:active { transform: scale(0.9); }
.status-answered { background: var(--success); color: white; border-color: var(--success); }
.status-marked { background: #a855f7; color: white; border-color: #a855f7; }
.status-current { border: 2px solid var(--accent-main); }

/* 4. MODAL FIX (Fixed the transparent overlap issue) */
.modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.6); backdrop-filter: blur(5px); z-index: 10000; display: flex; align-items: center; justify-content: center; opacity: 0; pointer-events: none; transition: 0.3s; }
.modal-overlay.active { opacity: 1; pointer-events: auto; }
.modal-content { background: var(--bg-surface); color: var(--text-primary); border: 1px solid var(--border-subtle); border-radius: 32px; padding: 32px 24px; text-align: center; width: 90%; max-width: 400px; box-shadow: 0 20px 40px rgba(0,0,0,0.5); transform: translateY(20px); transition: 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275); }
.modal-overlay.active .modal-content { transform: translateY(0); }

/* UTILS */
.hidden { display: none !important; }
.badge { padding: 4px 10px; border-radius: 8px; font-size: 11px; font-weight: 800; text-transform: uppercase; letter-spacing: 0.5px; display: inline-flex; align-items: center; gap: 4px; }
.bg-blue { background: rgba(99, 102, 241, 0.15); color: var(--accent-main); }
.bg-purple { background: rgba(168, 85, 247, 0.15); color: #a855f7; }
.bg-green { background: rgba(16, 185, 129, 0.15); color: var(--success); }
.bg-red { background: rgba(239, 68, 68, 0.15); color: var(--danger); }
.prose img { max-width: 100%; border-radius: 12px; margin: 10px 0; }
</style>
</head>
<body class="dark-mode">

<!-- WELCOME SCREEN -->
<div id="welcome-screen" class="min-h-screen flex items-center justify-center p-4">
    <div class="bento-card w-full max-w-lg text-center">
        <h1 class="text-2xl font-black mb-2" style="color: var(--accent-main);">_TEST_NAME_</h1>
        <p class="text-sm font-semibold" style="color: var(--text-secondary);">_TEST_SERIES_</p>
        
        <div class="grid grid-cols-2 gap-4 my-6">
            <div style="background: var(--bg-base); padding: 12px; border-radius: 16px; border: 1px solid var(--border-subtle);">
                <p style="font-size:24px; font-weight:900; color:var(--accent-main);" class="notranslate">_QUESTIONS_</p>
                <p style="font-size:11px; font-weight:700; color:var(--text-secondary); text-transform:uppercase;">Questions</p>
            </div>
            <div style="background: var(--bg-base); padding: 12px; border-radius: 16px; border: 1px solid var(--border-subtle);">
                <p style="font-size:24px; font-weight:900; color:var(--accent-main);" class="notranslate">_DURATION_</p>
                <p style="font-size:11px; font-weight:700; color:var(--text-secondary); text-transform:uppercase;">Duration</p>
            </div>
        </div>

        <div class="flex justify-center gap-4 mb-6">
            <span class="badge bg-green"><i class="fas fa-check"></i> _CORRECT_MARKS_DISPLAY_ Marks</span>
            <span class="badge bg-red"><i class="fas fa-times"></i> _INCORRECT_MARKS_DISPLAY_ Mark</span>
        </div>

        <button onclick="window.startQuiz()" class="btn-primary" style="width: 100%; justify-content: center; padding: 16px; font-size: 16px;">
            <i class="fas fa-play"></i> Start Engine
        </button>
    </div>
</div>

<!-- QUIZ SCREEN -->
<div id="quiz-screen" class="hidden">
    <!-- Sticky Header -->
    <header class="bento-header">
        <div id="timer" class="badge bg-blue" style="font-size: 14px; padding: 8px 12px;"><i class="fas fa-clock"></i> <span id="time" class="notranslate">00:00</span></div>
        <div class="flex gap-2">
            <button onclick="window.openLanguageModal()" class="btn-outline" style="padding: 8px 12px;"><i class="fas fa-language"></i></button>
            <button onclick="document.body.classList.toggle('dark-mode')" class="btn-outline" style="padding: 8px 12px;"><i class="fas fa-moon"></i></button>
            <button onclick="window.openQuestionNav()" class="btn-outline" style="padding: 8px 12px;"><i class="fas fa-th-large"></i></button>
            <button onclick="window.confirmSubmission()" class="btn-danger" style="padding: 8px 16px;">Submit</button>
        </div>
    </header>

    <main class="p-4 max-w-3xl mx-auto">
        <div class="bento-card mb-20">
            <div class="flex justify-between items-center border-b pb-4 mb-4" style="border-color: var(--border-subtle);">
                <h2 class="text-lg font-bold">Question <span id="question-number" class="notranslate">1</span></h2>
                <span class="badge bg-purple" id="q-lang-badge">EN</span>
            </div>
            <!-- Questions and Options -->
            <div id="question-container" class="prose max-w-none mb-6 question-content-area text-[15px] font-medium"></div>
            <div id="options-container"></div>
        </div>
    </main>

    <!-- Bottom Action Bar -->
    <footer style="position: fixed; bottom: 0; left: 0; right: 0; background: var(--bg-surface); padding: 12px 16px; border-top: 1px solid var(--border-subtle); display: flex; gap: 8px; z-index: 10;">
        <button onclick="window.clearResponse()" class="btn-outline" style="flex: 1;"><i class="fas fa-trash"></i></button>
        <button onclick="window.markForReview()" class="btn-outline" style="flex: 2; border-color: #a855f7; color: #a855f7;">Mark</button>
        <button onclick="window.saveAndNext()" class="btn-primary" style="flex: 3; justify-content: center;">Save & Next</button>
    </footer>
</div>

<!-- RESULTS SCREEN -->
<div id="results-screen" class="hidden min-h-screen flex items-center justify-center p-4">
    <div class="bento-card w-full max-w-lg text-center">
        <h1 class="text-2xl font-black mb-4">Report Card</h1>
        <div class="text-5xl font-black mb-6 notranslate" style="color: var(--accent-main);"><span id="final-score">0</span><span style="font-size:20px; color:var(--text-secondary);"> / <span id="total-score">0</span></span></div>
        
        <div class="grid grid-cols-2 gap-4 mb-8">
            <div style="background: var(--bg-base); padding: 16px; border-radius: 16px; border: 1px solid var(--border-subtle);">
                <p class="text-2xl font-black text-green-500 notranslate" id="correct-count">0</p>
                <p class="text-[10px] font-bold text-gray-500 uppercase">Correct</p>
            </div>
            <div style="background: var(--bg-base); padding: 16px; border-radius: 16px; border: 1px solid var(--border-subtle);">
                <p class="text-2xl font-black text-red-500 notranslate" id="incorrect-count">0</p>
                <p class="text-[10px] font-bold text-gray-500 uppercase">Incorrect</p>
            </div>
        </div>
        <button onclick="window.reviewAnswers()" class="btn-primary w-full justify-center" style="padding: 16px; font-size: 16px;"><i class="fas fa-eye"></i> Review Answers</button>
    </div>
</div>

<!-- REVIEW SCREEN -->
<div id="review-screen" class="hidden">
    <header class="bento-header">
        <div id="review-question-counter" class="font-bold text-lg"></div>
        <button onclick="window.backToResults()" class="btn-outline">Back</button>
    </header>
    
    <div class="p-4 max-w-3xl mx-auto mb-20">
        <div id="review-container"></div>
    </div>
    
    <footer style="position: fixed; bottom: 0; left: 0; right: 0; background: var(--bg-surface); padding: 12px 16px; border-top: 1px solid var(--border-subtle); display: flex; gap: 8px; z-index: 10;">
        <button id="prev-review-btn" onclick="window.prevReviewQuestion()" class="btn-outline" style="flex: 1; justify-content: center;"><i class="fas fa-chevron-left"></i> Prev</button>
        <button id="next-review-btn" onclick="window.nextReviewQuestion()" class="btn-primary" style="flex: 1; justify-content: center;">Next <i class="fas fa-chevron-right"></i></button>
    </footer>
</div>

<!-- MODALS (Fixed z-index and backgrounds) -->
<div id="question-nav-modal" class="modal-overlay" onclick="if(event.target===this) window.closeQuestionNav()">
    <div class="modal-content" style="max-width: 500px;">
        <div class="flex justify-between items-center mb-6">
            <h2 class="text-xl font-bold">Jump to Question</h2>
            <button onclick="window.closeQuestionNav()" class="text-2xl text-gray-400 hover:text-red-500">&times;</button>
        </div>
        <div id="question-grid" class="grid grid-cols-5 gap-3"></div>
    </div>
</div>

<div id="confirm-submit-modal" class="modal-overlay" onclick="if(event.target===this) window.closeConfirmSubmission()">
    <div class="modal-content">
        <i class="fas fa-triangle-exclamation" style="font-size: 48px; color: var(--warning); margin-bottom: 16px;"></i>
        <h2 class="text-xl font-black mb-2">Submit Exam?</h2>
        <p class="mb-6 font-medium text-sm" style="color: var(--text-secondary);">You have <span id="unanswered-modal-count" class="font-bold" style="color: var(--text-primary);">0</span> unanswered questions.</p>
        <div class="flex gap-4">
            <button onclick="window.closeConfirmSubmission()" class="btn-outline flex-1 justify-center">Cancel</button>
            <button onclick="window.submitQuiz()" class="btn-danger flex-1 justify-center">Yes, Submit</button>
        </div>
    </div>
</div>

<div id="language-modal" class="modal-overlay" onclick="if(event.target===this) window.closeLanguageModal()">
    <div class="modal-content">
        <div class="flex justify-between items-center mb-6">
            <h2 class="text-xl font-bold">Choose Language</h2>
            <button onclick="window.closeLanguageModal()" class="text-2xl text-gray-400 hover:text-red-500">&times;</button>
        </div>
        <div id="language-options-container"></div>
    </div>
</div>

<script>
    // --- GLOBAL DATA ---
    try {
        window.quizData = /* QUIZ_DATA_PLACEHOLDER */;
        window.timeRemaining = Number("_TIMER_SECONDS_") || 1800;
        window.CORRECT_MARKS = Number("_JS_CORRECT_MARKS_VALUE_") || 1;
        window.INC_MARKS = Number("_JS_INCORRECT_MARKS_VALUE_") || 0;
    } catch(e) {
        window.quizData = { title: "Error", questions: [] };
        window.timeRemaining = 1800; window.CORRECT_MARKS = 1; window.INC_MARKS = 0;
    }
    window.langDisplayNames = { 'en': 'English', 'hn': 'Hindi (हिन्दी)', 'hi': 'Hindi (हिन्दी)' };
    window.currentQuestionIndex = 0; window.currentReviewIndex = 0;
    window.userAnswers = []; window.questionStatus = []; window.score = 0;
    window.timer = null; window.currentLanguage = 'en';

    window.getEl = function(id) { return document.getElementById(id); };
    window.decodeHtml = function(html) {
        if (!html) return "";
        var txt = document.createElement("textarea"); txt.innerHTML = html;
        return txt.value.replace(/src=(["'])\/\//g, 'src=$1https://').replace(/src=(["'])\/([^\/])/g, 'src=$1https://testbook.com/$2');
    };
    window.getLocalizedContent = function(obj) {
        if (!obj) return "";
        if (obj[window.currentLanguage]) return obj[window.currentLanguage];
        if (obj['en']) return obj['en'];
        var keys = Object.keys(obj); return keys.length > 0 ? obj[keys[0]] : "";
    };

    // --- MATHJAX RENDER TRIGGER ---
    window.triggerMathJax = function() {
        if (window.MathJax && MathJax.typesetPromise) {
            MathJax.typesetPromise().catch(function (err) { console.error('MathJax Error:', err.message); });
        }
    };

    // --- MODALS ---
    window.openLanguageModal = function() { window.getEl('language-modal').classList.add('active'); };
    window.closeLanguageModal = function() { window.getEl('language-modal').classList.remove('active'); };
    window.openQuestionNav = function() { 
        var grid = window.getEl('question-grid'); grid.innerHTML = ''; 
        window.quizData.questions.forEach(function(_, i) { 
            var cls = ''; 
            if (window.questionStatus[i] === 'answered') cls = 'status-answered'; 
            if (window.questionStatus[i] === 'marked') cls = 'status-marked'; 
            var btn = document.createElement('div'); btn.textContent = i + 1; 
            btn.className = 'question-nav-btn ' + cls + ' ' + (i === window.currentQuestionIndex ? 'status-current' : '') + ' notranslate'; 
            btn.onclick = function() { window.loadQuestion(i); window.closeQuestionNav(); }; 
            grid.appendChild(btn); 
        }); 
        window.getEl('question-nav-modal').classList.add('active'); 
    };
    window.closeQuestionNav = function() { window.getEl('question-nav-modal').classList.remove('active'); };

    window.initLanguageSelector = function() {
        var langs = window.quizData.available_languages || [];
        var cont = window.getEl('language-options-container');
        if(!cont) return;
        cont.innerHTML = '';
        if (langs.length > 1) {
            langs.forEach(function(code) {
                var btn = document.createElement('div');
                btn.innerHTML = `<span>${window.langDisplayNames[code] || code.toUpperCase()}</span>`;
                btn.className = 'p-4 mb-2 border rounded-xl cursor-pointer font-bold transition ' + (code === window.currentLanguage ? 'border-indigo-500 bg-indigo-500/10 text-indigo-500' : 'border-gray-200 dark:border-gray-700');
                btn.onclick = function() {
                    window.currentLanguage = code;
                    window.getEl('q-lang-badge').innerText = code.toUpperCase();
                    if(!window.getEl('quiz-screen').classList.contains('hidden')) window.loadQuestion(window.currentQuestionIndex);
                    else if(!window.getEl('review-screen').classList.contains('hidden')) window.loadReviewQuestion(window.currentReviewIndex);
                    window.closeLanguageModal(); window.initLanguageSelector();
                };
                cont.appendChild(btn);
            });
        } else {
            window.getEl('q-lang-badge').innerText = 'EN';
        }
    };

    window.initQuiz = function() {
         window.userAnswers = new Array(window.quizData.questions.length).fill(null);
         window.questionStatus = new Array(window.quizData.questions.length).fill('not-answered');
         window.initLanguageSelector();
    };
    window.startQuiz = function() {
        window.getEl('welcome-screen').classList.add('hidden');
        window.getEl('quiz-screen').classList.remove('hidden');
        window.initQuiz();
        window.loadQuestion(0);
        
        clearInterval(window.timer);
        window.timer = setInterval(function() {
            window.timeRemaining--;
            var m = Math.floor(window.timeRemaining / 60).toString().padStart(2, '0');
            var s = (window.timeRemaining % 60).toString().padStart(2, '0');
            window.getEl('time').textContent = m + ':' + s;
            if (window.timeRemaining <= 0) { clearInterval(window.timer); window.submitQuiz(); }
        }, 1000);
    };

    window.loadQuestion = function(index) {
        if (index < 0 || index >= window.quizData.questions.length) return;
        window.currentQuestionIndex = index;
        window.questionStatus[index] = 'current';
        var q = window.quizData.questions[index];
        window.getEl('question-number').textContent = index + 1;
        window.getEl('question-container').innerHTML = window.decodeHtml(window.getLocalizedContent(q.content));
        var optsCont = window.getEl('options-container');
        optsCont.innerHTML = '';
        var opts = window.getLocalizedContent(q.options);
        if (Array.isArray(opts)) {
            opts.forEach(function(opt, i) {
                var isSelected = window.userAnswers[index] === i;
                var id = 'opt-' + i;
                optsCont.insertAdjacentHTML('beforeend',
                    `<div class="option-row ${isSelected ? 'selected' : ''}" onclick="window.selectOption(${i})">
                        <div class="mr-3"><input type="radio" id="${id}" class="option-radio" ${isSelected ? 'checked' : ''}></div>
                        <div class="flex-1 overflow-x-auto">${window.decodeHtml(opt.text)}</div>
                    </div>`
                );
            });
        }
        window.triggerMathJax(); // FIX: Trigger MathJax on load!
    };

    window.selectOption = function(i) { window.userAnswers[window.currentQuestionIndex] = i; window.loadQuestion(window.currentQuestionIndex); };
    window.clearResponse = function() { window.userAnswers[window.currentQuestionIndex] = null; window.loadQuestion(window.currentQuestionIndex); };
    window.saveAndNext = function() { window.questionStatus[window.currentQuestionIndex] = window.userAnswers[window.currentQuestionIndex] !== null ? 'answered' : 'not-answered'; if (window.currentQuestionIndex < window.quizData.questions.length - 1) window.loadQuestion(window.currentQuestionIndex + 1); else window.confirmSubmission(); };
    window.markForReview = function() { window.questionStatus[window.currentQuestionIndex] = 'marked'; if (window.currentQuestionIndex < window.quizData.questions.length - 1) window.loadQuestion(window.currentQuestionIndex + 1); else window.confirmSubmission(); };

    window.confirmSubmission = function() {
        window.getEl('unanswered-modal-count').textContent = window.userAnswers.filter(function(a) { return a === null; }).length;
        window.getEl('confirm-submit-modal').classList.add('active');
    };
    window.closeConfirmSubmission = function() { window.getEl('confirm-submit-modal').classList.remove('active'); };

    window.submitQuiz = function() {
        window.closeConfirmSubmission();
        clearInterval(window.timer);
        window.score = 0;
        window.quizData.questions.forEach(function(q, i) {
            var ans = window.userAnswers[i];
            if (ans !== null) { 
                var opts = q.options.en || q.options[Object.keys(q.options)[0]]; 
                if (opts && opts[ans] && opts[ans].is_correct) window.score += window.CORRECT_MARKS; 
                else window.score -= window.INC_MARKS; 
            }
        });
        window.getEl('quiz-screen').classList.add('hidden');
        window.getEl('results-screen').classList.remove('hidden');
        window.getEl('final-score').textContent = window.score.toFixed(2);
        window.getEl('total-score').textContent = (window.quizData.questions.length * window.CORRECT_MARKS).toFixed(2);
        var c=0, i=0, u=0;
        for(var j=0; j<window.userAnswers.length; j++) {
            if(window.userAnswers[j]===null) u++;
            else { var getOpts = function(q) { return q.options.en || q.options[Object.keys(q.options)[0]]; }; if(getOpts(window.quizData.questions[j])[window.userAnswers[j]].is_correct) c++; else i++; }
        }
        window.getEl('correct-count').textContent = c;
        window.getEl('incorrect-count').textContent = i;
    };

    window.reviewAnswers = function() { 
        window.getEl('results-screen').classList.add('hidden'); 
        window.getEl('review-screen').classList.remove('hidden'); 
        window.loadReviewQuestion(0); 
    };
    window.loadReviewQuestion = function(index) {
        if (index < 0 || index >= window.quizData.questions.length) return;
        window.currentReviewIndex = index;
        var container = window.getEl('review-container'); 
        var q = window.quizData.questions[index]; 
        var content = window.getLocalizedContent(q.content); 
        var opts = window.getLocalizedContent(q.options); 
        var sol = window.getLocalizedContent(q.solution); 
        var userAns = window.userAnswers[index]; 
        var correctIdx = -1; 
        if (Array.isArray(opts)) opts.forEach(function(o, i) { if(o.is_correct) correctIdx = i; }); 
        
        var optsHtml = ''; 
        if (Array.isArray(opts)) { 
            opts.forEach(function(opt, i) { 
                var cls = ''; var icon = '<div style="width:20px; height:20px; border-radius:50%; border:2px solid var(--border-subtle);"></div>'; 
                if (i === correctIdx) { cls = 'border-green-500 bg-green-500/10'; icon = '<i class="fas fa-check-circle text-green-500 text-xl"></i>'; } 
                else if (i === userAns) { cls = 'border-red-500 bg-red-500/10'; icon = '<i class="fas fa-times-circle text-red-500 text-xl"></i>'; } 
                optsHtml += `<div class="option-row ${cls}"><div class="mr-3 mt-1">${icon}</div><div class="flex-1 overflow-x-auto">${window.decodeHtml(opt.text)}</div></div>`; 
            }); 
        }
        container.innerHTML = `<div class="bento-card"><h3 class="font-bold text-lg mb-4 border-b pb-2" style="border-color:var(--border-subtle);">Question ${index + 1}</h3><div class="prose max-w-none mb-6 overflow-x-auto font-medium text-[15px]">${window.decodeHtml(content)}</div><div>${optsHtml}</div><div class="mt-6 p-4 rounded-xl border border-green-500/50 bg-green-500/10"><h4 class="font-bold text-green-500 mb-2"><i class="fas fa-lightbulb"></i> Solution</h4><div class="prose max-w-none overflow-x-auto text-sm">${window.decodeHtml(sol)}</div></div></div>`;
        window.getEl('review-question-counter').textContent = 'Q ' + (index + 1) + ' / ' + window.quizData.questions.length;
        window.getEl('prev-review-btn').disabled = index === 0; 
        window.getEl('next-review-btn').disabled = index === window.quizData.questions.length - 1; 
        
        window.triggerMathJax(); // FIX: Trigger MathJax on Review load too!
    };
    window.prevReviewQuestion = function() { window.loadReviewQuestion(window.currentReviewIndex - 1); }; 
    window.nextReviewQuestion = function() { window.loadReviewQuestion(window.currentReviewIndex + 1); }; 
    window.backToResults = function() { window.getEl('review-screen').classList.add('hidden'); window.getEl('results-screen').classList.remove('hidden'); };
</script>
</body>
</html>
"""

def generate_html(quiz_data: dict, details: dict) -> str:
    processed_content_str = json.dumps(quiz_data, ensure_ascii=False)
    final_html = HTML_TEMPLATE.replace('/* QUIZ_DATA_PLACEHOLDER */', processed_content_str)
    
    try:
        dur_str = details.get('Duration', '30 minutes')
        dur_int = int(re.search(r'\d+', dur_str).group()) * 60
    except:
        dur_int = 1800
    
    replacements = {
        '_TEST_NAME_': details.get('Test Name', quiz_data.get('title', 'Mock Test')),
        '_TEST_SERIES_': details.get('Test Series', ''),
        '_SECTION_': details.get('Section', 'N/A'),
        '_SUBSECTION_': details.get('Subsection', 'N/A'),
        '_QUESTIONS_': details.get('Questions', str(len(quiz_data.get("questions", [])))),
        '_DURATION_': details.get('Duration', '30 minutes'),
        '_TIMER_SECONDS_': str(dur_int),
        '_TOTAL_MARKS_': details.get('Total Marks', 'N/A'),
        '_CORRECT_MARKS_DISPLAY_': details.get('Correct', '+1'),
        '_INCORRECT_MARKS_DISPLAY_': details.get('Incorrect', '-0.25'),
        '_JS_CORRECT_MARKS_VALUE_': str(float(re.search(r'([+-]?\d+\.?\d*)', details.get('Correct', '1')).group(1) or 1)),
        '_JS_INCORRECT_MARKS_VALUE_': str(float(re.search(r'([+-]?\d+\.?\d*)', details.get('Incorrect', '0')).group(1) or 0)),
    }
    
    for k, v in replacements.items(): 
        final_html = final_html.replace(k, str(v))
        
    return final_html
