import json
import re
import base64

# --- HTML Template (Online & Offline Base) ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>H4R Test</title>
<script src="https://cdn.tailwindcss.com"></script>
<link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
<script>
    tailwind.config = {
        darkMode: 'class',
        theme: {
            extend: {
                colors: {
                    gray: { 900: '#111827', 800: '#1f2937', 700: '#374151' }
                }
            }
        }
    }
</script>
<style>
:root {
    --bg-light: #f8f9fa; --text-light: #212529; --card-light: #ffffff;
    --bg-dark: #111827; --text-dark: #f3f4f6; --card-dark: #1f2937;
}
body { background-color: var(--bg-light); color: var(--text-light); }
.card { background-color: var(--card-light); border: 1px solid #e5e7eb; }
.dark-mode body { background-color: var(--bg-dark) !important; color: var(--text-dark) !important; }
.dark-mode .card { background-color: var(--card-dark) !important; border-color: #374151 !important; color: var(--text-dark) !important; }
.dark-mode .modal-content { background-color: var(--card-dark) !important; border: 1px solid #374151 !important; color: var(--text-dark) !important; }
.dark-mode h1, .dark-mode h2, .dark-mode h3, .dark-mode h4, .dark-mode p, .dark-mode span, .dark-mode div, .dark-mode li, .dark-mode strong { color: var(--text-dark) !important; }
.question-content-area { overflow-x: auto !important; width: 100%; display: block; -webkit-overflow-scrolling: touch; }
#question-grid { max-height: 60vh; overflow-y: auto; padding-right: 5px; }
#question-grid::-webkit-scrollbar { width: 6px; }
#question-grid::-webkit-scrollbar-thumb { background-color: #cbd5e1; border-radius: 3px; }
.dark-mode #question-grid::-webkit-scrollbar-thumb { background-color: #4b5563; }
.option-radio { -webkit-appearance: none; appearance: none; border: 2px solid #e5e7eb; border-radius: 50%; width: 20px; height: 20px; cursor: pointer; transition: all .2s; position: relative; }
.dark-mode .option-radio { border-color: #4b5563; }
.option-radio:checked { border-color: #4f46e5; background-color: #4f46e5; }
.option-radio:checked::after { content: ''; display: block; width: 8px; height: 8px; background: white; border-radius: 50%; position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); }
.question-nav-btn { width: 40px; height: 40px; display: flex; align-items: center; justify-content: center; border-radius: 8px; font-weight: bold; transition: all .2s; }
.status-not-answered { background-color: #e5e7eb; color: #374151; }
.dark-mode .status-not-answered { background-color: #374151; color: #f9fafb; }
.status-answered { background-color: #22c55e; color: white !important; }
.status-marked { background-color: #8b5cf6; color: white !important; }
.status-current { border: 2px solid #4f46e5; }
.hidden { display: none; }
.prose img { max-width: 100%; height: auto; border-radius: .5rem; margin: 10px 0; }
#language-options-container { display: flex; flex-direction: column; gap: 10px; padding: 5px; }
.modal-lang-btn { width: 100%; text-align: left; padding: 12px 16px; border-radius: 8px; font-weight: 600; transition: all 0.2s; border: 1px solid #e5e7eb; background-color: white; color: #374151; white-space: normal; word-break: break-word; }
.dark-mode .modal-lang-btn { background-color: #1f2937; border-color: #4b5563; color: #f3f4f6; }
.modal-lang-btn:hover { background-color: #f3f4f6; border-color: #d1d5db; }
.dark-mode .modal-lang-btn:hover { background-color: #374151; border-color: #6b7280; }
.modal-lang-btn-active { background-color: #e0e7ff !important; border-color: #4f46e5 !important; color: #4f46e5 !important; }
.dark-mode .modal-lang-btn-active { background-color: #312e81 !important; border-color: #6366f1 !important; color: #818cf8 !important; }
</style>
</head>
<body class="font-sans dark-mode transition-colors duration-300">
<!-- WELCOME SCREEN -->
<div id="welcome-screen" class="min-h-screen flex items-center justify-center p-4">
    <div class="card w-full max-w-lg p-6 sm:p-8 rounded-xl shadow-lg">
        <div class="text-center">
            <h1 class="text-2xl sm:text-3xl font-bold text-indigo-600 dark:text-indigo-400">Welcome to H4R Test!</h1>
            <p class="mt-2 opacity-70">Aapki mehnat hi aapki saflta ki kunji hai.</p>
        </div>
        <div class="border-t border-gray-200 dark:border-gray-700 mt-6 pt-6">
            <h2 class="text-xl font-bold mb-4 text-center">  Test Details  </h2>
            <div class="text-center">
                <h3 class="text-xl sm:text-2xl font-bold">_TEST_NAME_</h3>
                <p class="text-sm opacity-70 mt-1">_TEST_SERIES_</p>
            </div>
            <div class="border-t border-b border-gray-200 dark:border-gray-700 py-4 my-6 text-left space-y-3">
                <div class="flex justify-between items-center"><span class="font-semibold">  Section:</span><span class="opacity-80 text-right">_SECTION_</span></div>
                <div class="flex justify-between items-center"><span class="font-semibold">  Subsection:</span><span class="opacity-80 text-right">_SUBSECTION_</span></div>
                <div class="flex justify-between items-center"><span class="font-semibold">  Correct:</span><span class="text-green-500 font-bold text-right notranslate">_CORRECT_MARKS_DISPLAY_</span></div>
                <div class="flex justify-between items-center"><span class="font-semibold">  Incorrect:</span><span class="text-red-500 font-bold text-right notranslate">_INCORRECT_MARKS_DISPLAY_</span></div>
            </div>
            <div class="grid grid-cols-3 gap-4 text-center">
                <div><p class="text-2xl font-bold text-indigo-500 dark:text-indigo-400 notranslate">_QUESTIONS_</p><p class="text-xs opacity-70">Questions</p></div>
                <div><p class="text-2xl font-bold text-indigo-500 dark:text-indigo-400 notranslate">_DURATION_</p><p class="text-xs opacity-70">Duration</p></div>
                <div><p class="text-2xl font-bold text-indigo-500 dark:text-indigo-400 notranslate">_TOTAL_MARKS_</p><p class="text-xs opacity-70">Marks</p></div>
            </div>
        </div>
        <div class="mt-8 space-y-4">
             <button id="start-quiz-btn" onclick="window.startQuiz()" class="w-full bg-indigo-600 text-white py-3 rounded-lg font-semibold hover:bg-indigo-700 transition duration-300 flex items-center justify-center gap-2"><i class="fas fa-play"></i> Start Test</button>
        </div>
    </div>
</div>

<!-- QUIZ SCREEN -->
<div id="quiz-screen" class="hidden">
    <header class="card sticky top-0 z-10 p-3 sm:p-4 shadow-md rounded-none flex flex-col sm:flex-row justify-between items-center gap-2">
        <div class="text-center sm:text-left"><h1 id="quiz-title" class="text-lg sm:text-xl font-bold"></h1><div class="flex gap-4 text-xs sm:text-sm mt-1"><span><i class="fas fa-check text-green-500"></i> <span class="notranslate">_CORRECT_MARKS_DISPLAY_</span> Marks</span><span><i class="fas fa-times text-red-500"></i> <span class="notranslate">_INCORRECT_MARKS_DISPLAY_</span> Mark</span></div></div>
        <div class="flex items-center gap-2 sm:gap-4">
            <div id="timer" class="text-base sm:text-lg font-bold bg-blue-200 dark:bg-blue-700 px-3 py-1.5 sm:px-4 sm:py-2 rounded-lg notranslate"><i class="fas fa-clock"></i> <span id="time">00:00</span></div>
            <button id="lang-switch-btn" onclick="window.openLanguageModal()" class="bg-green-500 text-white px-3 py-1.5 sm:px-4 sm:py-2 rounded-lg hover:bg-green-600"><i class="fas fa-language"></i></button>
            <button id="theme-toggle" class="text-xl px-2"><i class="fas fa-sun"></i></button>
            <button onclick="window.openQuestionNav()" class="bg-indigo-500 text-white px-3 py-1.5 sm:px-4 sm:py-2 rounded-lg hover:bg-indigo-600"><i class="fas fa-th-large"></i></button>
            <button onclick="window.confirmSubmission()" class="bg-red-500 text-white px-3 py-1.5 sm:px-4 sm:py-2 rounded-lg hover:bg-red-600 font-semibold">Submit</button>
        </div>
    </header>
    <main class="p-4 md:p-8 max-w-6xl mx-auto">
        <div class="card p-4 sm:p-6 rounded-xl shadow-lg">
            <div class="flex justify-between items-center border-b pb-4 mb-4 dark:border-gray-600">
                <h2 class="text-lg sm:text-xl font-semibold">Question <span id="question-number" class="notranslate">1</span></h2>
            </div>
            <div id="question-container" class="prose max-w-none mb-6 question-content-area"></div>
            <div id="options-container" class="space-y-4"></div>
        </div>
        <footer class="mt-6 flex flex-col sm:flex-row justify-between items-center gap-4">
            <div class="flex gap-4 w-full sm:w-auto"><button onclick="window.clearResponse()" class="flex-1 sm:flex-initial bg-blue-400 text-white px-4 py-2 sm:px-6 sm:py-3 rounded-lg hover:bg-blue-800 transition font-semibold"><i class="fas fa-trash"></i> Clear</button></div>
            <div class="flex gap-4 w-full sm:w-auto"><button onclick="window.markForReview()" class="flex-1 sm:flex-initial bg-purple-500 text-white px-4 py-2 sm:px-6 sm:py-3 rounded-lg hover:bg-purple-600 transition font-semibold text-xs sm:text-sm">Mark & Next</button><button onclick="window.saveAndNext()" class="flex-1 sm:flex-initial bg-green-500 text-white px-4 py-2 sm:px-6 sm:py-3 rounded-lg hover:bg-green-600 transition font-semibold text-xs sm:text-sm">Save & Next</button></div>
        </footer>
    </main>
</div>

<!-- RESULTS SCREEN -->
<div id="results-screen" class="hidden min-h-screen flex items-center justify-center p-4">
    <div class="card w-full max-w-lg text-center p-6 sm:p-8 rounded-xl shadow-lg"><h1 class="text-2xl sm:text-3xl font-bold mb-4">Your Results</h1><div class="text-4xl sm:text-5xl font-bold text-indigo-600 dark:text-indigo-400 mb-6 notranslate"><span id="final-score">0</span> / <span id="total-score">0</span></div><div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8 text-center"><div><p class="text-2xl sm:text-3xl font-bold text-green-500 notranslate" id="correct-count">0</p><p class="text-xs sm:text-sm opacity-70">Correct</p></div><div><p class="text-2xl sm:text-3xl font-bold text-red-500 notranslate" id="incorrect-count">0</p><p class="text-xs sm:text-sm opacity-70">Incorrect</p></div><div><p class="text-2xl sm:text-3xl font-bold text-blue-500 notranslate" id="unanswered-count">0</p><p class="text-xs sm:text-sm opacity-70">Unanswered</p></div><div><p class="text-2xl sm:text-3xl font-bold text-purple-500 notranslate" id="marked-count-result">0</p><p class="text-xs sm:text-sm opacity-70">Marked</p></div></div><div class="flex flex-col sm:flex-row gap-4"><button onclick="window.reviewAnswers()" class="flex-1 bg-indigo-600 text-white py-3 rounded-lg font-semibold hover:bg-indigo-700 transition"><i class="fas fa-eye"></i> Review Answers</button></div></div>
</div>

<!-- REVIEW SCREEN -->
<div id="review-screen" class="hidden p-4 md:p-8 max-w-7xl mx-auto">
    <header class="card sticky top-0 z-10 p-3 sm:p-4 shadow-md flex justify-between items-center mb-8"><div id="review-question-counter" class="text-lg sm:text-xl font-bold"></div><div class="flex items-center gap-2 sm:gap-4">
    <button id="review-lang-switch-btn" onclick="window.openLanguageModal()" class="bg-green-500 text-white px-3 py-1.5 sm:px-4 sm:py-2 rounded-lg hover:bg-green-600"><i class="fas fa-language"></i></button>
    <button id="review-theme-toggle" class="text-xl px-2"><i class="fas fa-sun"></i></button><button onclick="window.backToResults()" class="bg-indigo-600 text-white px-4 py-2 sm:px-5 sm:py-2.5 rounded-lg hover:bg-indigo-700 font-semibold">Back</button></div></header><div class="flex flex-col lg:flex-row gap-8"><div class="w-full lg:w-3/4"><div id="review-container"></div><footer class="mt-8 flex justify-between items-center"><button id="prev-review-btn" onclick="window.prevReviewQuestion()" class="bg-indigo-500 text-white px-4 py-2 sm:px-6 sm:py-3 rounded-lg hover:bg-indigo-600 transition font-semibold flex items-center gap-2 disabled:opacity-50"><i class="fas fa-chevron-left"></i> Previous</button><button id="next-review-btn" onclick="window.nextReviewQuestion()" class="bg-indigo-500 text-white px-4 py-2 sm:px-6 sm:py-3 rounded-lg hover:bg-indigo-600 transition font-semibold flex items-center gap-2 disabled:opacity-50">Next <i class="fas fa-chevron-right"></i></button></footer></div><aside class="w-full lg:w-1/4"><div class="card p-4 rounded-xl shadow-lg"><h3 class="font-bold mb-4">Question Palette</h3><div id="review-palette-grid" class="grid grid-cols-5 sm:grid-cols-6 md:grid-cols-4 lg:grid-cols-5 gap-2"></div><div class="mt-4 space-y-2 text-xs"><div class="flex items-center gap-2"><div class="w-4 h-4 rounded-sm review-status-correct" style="background-color:#22c55e"></div> Correct</div><div class="flex items-center gap-2"><div class="w-4 h-4 rounded-sm review-status-incorrect" style="background-color:#ef4444"></div> Incorrect</div><div class="flex items-center gap-2"><div class="w-4 h-4 rounded-sm review-status-unanswered" style="background-color:#d1d5db"></div> Unanswered</div><div class="flex items-center gap-2"><div class="w-4 h-4 rounded-sm review-status-marked" style="background-color:#8b5cf6"></div> Marked</div></div></div></aside></div>
</div>

<!-- MODALS -->
<div id="question-nav-modal" class="hidden fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4"><div class="modal-content w-full max-w-2xl p-4 sm:p-6 rounded-xl shadow-lg"><div class="flex justify-between items-center mb-4"><h2 class="text-2xl font-bold">Questions</h2><button onclick="window.closeQuestionNav()" class="text-2xl">&times;</button></div> <div id="question-grid" class="grid grid-cols-5 sm:grid-cols-8 md:grid-cols-10 gap-3"></div></div></div>
<div id="confirm-submit-modal" class="hidden fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4"><div class="modal-content w-full max-w-md text-center p-6 sm:p-8 rounded-xl shadow-lg"><h2 class="text-2xl font-bold mb-4">H4R Test</h2><p class="mb-6">You have <span id="unanswered-modal-count" class="font-bold">0</span> unanswered questions. Are you sure you want to submit?</p><div class="flex gap-4"><button onclick="window.closeConfirmSubmission()" class="flex-1 bg-blue-200 dark:bg-blue-600 py-3 rounded-lg font-semibold hover:bg-blue-300 dark:hover:bg-blue-700 transition">CANCEL</button><button onclick="window.submitQuiz()" class="flex-1 bg-red-500 text-white py-3 rounded-lg font-semibold hover:bg-red-600 transition">OK</button></div></div></div>
<div id="language-modal" class="hidden fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
    <div class="modal-content w-full max-w-sm p-4 sm:p-6 rounded-xl shadow-lg max-h-[80vh] flex flex-col">
        <div class="flex justify-between items-center mb-4 flex-shrink-0">
            <h2 class="text-xl font-bold">Choose Language</h2>
            <button onclick="window.closeLanguageModal()" class="text-2xl p-2 hover:bg-gray-100 rounded-full dark:hover:bg-gray-700">&times;</button>
        </div>
        <div id="language-options-container" class="overflow-y-auto pr-2"></div>
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
        window.timeRemaining = 1800;
        window.CORRECT_MARKS = 1;
        window.INC_MARKS = 0;
    }
    window.langDisplayNames = { 'en': 'English', 'hn': 'Hindi (हिन्दी)', 'hi': 'Hindi (हिन्दी)', 'mr': 'Marathi', 'te': 'Telugu', 'ta': 'Tamil', 'bn': 'Bengali' };
    window.currentQuestionIndex = 0;
    window.currentReviewIndex = 0;
    window.userAnswers = [];
    window.questionStatus = [];
    window.score = 0;
    window.timer = null;
    window.currentLanguage = 'en';

    window.getEl = function(id) { return document.getElementById(id); };
    window.decodeHtml = function(html) {
        if (!html) return "";
        var txt = document.createElement("textarea");
        txt.innerHTML = html;
        return txt.value.replace(/src=(["'])\/\//g, 'src=$1https://').replace(/src=(["'])\/([^\/])/g, 'src=$1https://testbook.com/$2');
    };
    window.getLocalizedContent = function(obj) {
        if (!obj) return "";
        if (obj[window.currentLanguage]) return obj[window.currentLanguage];
        if (obj['en']) return obj['en'];
        var keys = Object.keys(obj);
        return keys.length > 0 ? obj[keys[0]] : "";
    };

    window.openLanguageModal = function() { window.getEl('language-modal').classList.remove('hidden'); };
    window.closeLanguageModal = function() { window.getEl('language-modal').classList.add('hidden'); };
    window.initLanguageSelector = function() {
        var langs = window.quizData.available_languages || [];
        var cont = window.getEl('language-options-container');
        if(!cont) return;
        cont.innerHTML = '';
        if (langs.length > 1) {
            langs.forEach(function(code) {
                var btn = document.createElement('button');
                var displayName = window.langDisplayNames[code] || code.toUpperCase();
                btn.innerHTML = `<span>${displayName}</span>`;
                if (code === window.currentLanguage) {
                    btn.className = 'modal-lang-btn modal-lang-btn-active';
                    btn.innerHTML += ' <i class="fas fa-check float-right mt-1"></i>';
                } else {
                    btn.className = 'modal-lang-btn';
                }
                btn.onclick = function() {
                    window.currentLanguage = code;
                    if(!window.getEl('quiz-screen').classList.contains('hidden')) window.loadQuestion(window.currentQuestionIndex);
                    else if(!window.getEl('review-screen').classList.contains('hidden')) window.loadReviewQuestion(window.currentReviewIndex);
                    window.closeLanguageModal();
                    window.initLanguageSelector();
                };
                cont.appendChild(btn);
            });
        } else {
            var lBtn = window.getEl('lang-switch-btn'); if(lBtn) lBtn.classList.add('hidden');
            var rBtn = window.getEl('review-lang-switch-btn'); if(rBtn) rBtn.classList.add('hidden');
        }
    };

    window.initQuiz = function() {
         window.getEl('quiz-title').textContent = window.quizData.title;
         window.userAnswers = new Array(window.quizData.questions.length).fill(null);
         window.questionStatus = new Array(window.quizData.questions.length).fill('not-answered');
         window.initLanguageSelector();
     };
    window.startQuiz = function() {
         try {
            window.getEl('welcome-screen').classList.add('hidden');
            window.getEl('quiz-screen').classList.remove('hidden');
            window.initQuiz();
            window.loadQuestion(0);
            window.startTimer();
         } catch (e) {
            window.getEl('welcome-screen').classList.remove('hidden');
            window.getEl('quiz-screen').classList.add('hidden');
         }
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
                var id = 'option-' + index + '-' + i;
                optsCont.insertAdjacentHTML('beforeend',
                    '<label for="' + id + '" class="flex items-start sm:items-center gap-4 p-4 border rounded-lg cursor-pointer transition hover:border-indigo-500 dark:border-gray-600 dark:hover:border-indigo-500 ' + (isSelected ? 'border-indigo-600 bg-indigo-50 dark:bg-indigo-900/50' : '') + '">' +
                        '<input type="radio" id="' + id + '" name="option" value="' + i + '" class="option-radio mt-1 sm:mt-0" onchange="window.selectOption(' + i + ')" ' + (isSelected ? 'checked' : '') + '>' +
                        '<div class="prose max-w-none flex-1 overflow-x-auto dark:text-gray-200">' + window.decodeHtml(opt.text) + '</div>' +
                    '</label>'
                );
            });
        }
    };
    window.selectOption = function(i) { window.userAnswers[window.currentQuestionIndex] = i; window.loadQuestion(window.currentQuestionIndex); };
    window.clearResponse = function() { window.userAnswers[window.currentQuestionIndex] = null; window.loadQuestion(window.currentQuestionIndex); };
    window.saveAndNext = function() { window.questionStatus[window.currentQuestionIndex] = window.userAnswers[window.currentQuestionIndex] !== null ? 'answered' : 'not-answered'; if (window.currentQuestionIndex < window.quizData.questions.length - 1) window.loadQuestion(window.currentQuestionIndex + 1); else window.confirmSubmission(); };
    window.markForReview = function() { window.questionStatus[window.currentQuestionIndex] = 'marked'; if (window.currentQuestionIndex < window.quizData.questions.length - 1) window.loadQuestion(window.currentQuestionIndex + 1); else window.confirmSubmission(); };
    window.startTimer = function() {
        clearInterval(window.timer);
        window.timer = setInterval(function() {
            window.timeRemaining--;
            var m = Math.floor(window.timeRemaining / 60).toString().padStart(2, '0');
            var s = (window.timeRemaining % 60).toString().padStart(2, '0');
            window.getEl('time').textContent = m + ':' + s;
            if (window.timeRemaining <= 0) { clearInterval(window.timer); window.submitQuiz(); }
        }, 1000);
    };

    window.confirmSubmission = function() {
        window.getEl('unanswered-modal-count').textContent = window.userAnswers.filter(function(a) { return a === null; }).length;
        window.getEl('confirm-submit-modal').classList.remove('hidden');
    };
    window.closeConfirmSubmission = function() { window.getEl('confirm-submit-modal').classList.add('hidden'); };

    window.submitQuiz = function() {
        window.closeConfirmSubmission();
        clearInterval(window.timer);
        window.calculateScore();
        window.showResults();
    };
    window.calculateScore = function() {
        window.score = 0;
        window.quizData.questions.forEach(function(q, i) {
            var ans = window.userAnswers[i];
            if (ans !== null) { var opts = q.options.en || q.options[Object.keys(q.options)[0]]; if (opts && opts[ans] && opts[ans].is_correct) window.score += window.CORRECT_MARKS; else window.score -= window.INC_MARKS; }
        });
    };
    window.showResults = function() {
        window.getEl('quiz-screen').classList.add('hidden');
        window.getEl('results-screen').classList.remove('hidden');
        var getOpts = function(q) { return q.options.en || q.options[Object.keys(q.options)[0]]; };
        window.getEl('final-score').textContent = window.score.toFixed(2);
        window.getEl('total-score').textContent = (window.quizData.questions.length * window.CORRECT_MARKS).toFixed(2);
        var c=0, i=0, u=0, m=0;
        for(var j=0; j<window.userAnswers.length; j++) {
            if(window.userAnswers[j]===null) u++;
            else if(getOpts(window.quizData.questions[j])[window.userAnswers[j]].is_correct) c++;
            else i++;
            if(window.questionStatus[j]==='marked') m++;
        }
        window.getEl('correct-count').textContent = c;
        window.getEl('incorrect-count').textContent = i;
        window.getEl('unanswered-count').textContent = u;
        window.getEl('marked-count-result').textContent = m;
    };

    window.reviewAnswers = function() { window.getEl('results-screen').classList.add('hidden'); window.getEl('review-screen').classList.remove('hidden'); window.currentReviewIndex = 0; window.loadReviewQuestion(0); window.populateReviewPalette(); };
    window.loadReviewQuestion = function(index) {
        try {
            if (index < 0 || index >= window.quizData.questions.length) return;
            window.currentReviewIndex = index;
            var container = window.getEl('review-container'); container.innerHTML = '';
            var q = window.quizData.questions[index]; var content = window.getLocalizedContent(q.content); var opts = window.getLocalizedContent(q.options); var sol = window.getLocalizedContent(q.solution); var userAns = window.userAnswers[index]; var correctIdx = -1; if (Array.isArray(opts)) opts.forEach(function(o, i) { if(o.is_correct) correctIdx = i; }); var optsHtml = ''; if (Array.isArray(opts)) { opts.forEach(function(opt, i) { var cls = '', icon = ''; if (i === correctIdx) { cls = 'bg-green-100 dark:bg-green-900/50 border-green-500 force-black-text'; icon = '<i class="fas fa-check-circle text-green-600"></i>'; } else if (i === userAns) { cls = 'bg-red-100 dark:bg-red-900/50 border-red-500'; icon = '<i class="fas fa-times-circle text-red-600"></i>'; } optsHtml += '<div class="flex items-start sm:items-center gap-4 p-3 border rounded-lg ' + cls + ' dark:border-gray-600"><div class="w-6 mt-1 sm:mt-0">' + icon + '</div><div class="prose max-w-none flex-1 overflow-x-auto dark:text-gray-200">' + window.decodeHtml(opt.text) + '</div></div>'; }); }
            container.innerHTML = '<div class="card p-4 sm:p-6 rounded-xl shadow-md"><p class="font-semibold mb-2 dark:text-white">Question <span class="notranslate">' + (index + 1) + '</span></p><div class="prose max-w-none mb-4 overflow-x-auto dark:text-gray-200">' + window.decodeHtml(content) + '</div><div class="space-y-3 mb-4">' + optsHtml + '</div><div class="mt-4 p-4 bg-gray-100 dark:bg-gray-800 rounded-lg border-t-4 border-green-500"><h4 class="font-bold mb-2 text-green-700 dark:text-green-400">Solution</h4><div class="prose max-w-none force-black-text overflow-x-auto">' + window.decodeHtml(sol) + '</div></div></div>';
            window.getEl('review-question-counter').textContent = 'Question ' + (index + 1) + ' of ' + window.quizData.questions.length;
            window.getEl('prev-review-btn').disabled = index === 0; window.getEl('next-review-btn').disabled = index === window.quizData.questions.length - 1; window.updatePaletteHighlight();
        } catch (e) {}
    };
    window.populateReviewPalette = function() {
        var grid = window.getEl('review-palette-grid'); grid.innerHTML = '';
        var getCheckOptions = function(q) { return q.options.en || q.options[Object.keys(q.options)[0]]; };
        window.quizData.questions.forEach(function(q, i) {
            var btn = document.createElement('button'); btn.textContent = i + 1; btn.className = 'question-nav-btn notranslate';
            var ua = window.userAnswers[i]; var opts = getCheckOptions(q); var isCorrect = ua !== null && opts && opts[ua] && opts[ua].is_correct;
            if (isCorrect) btn.classList.add('review-status-correct'); else if (ua !== null) btn.classList.add('review-status-incorrect'); else if (window.questionStatus[i] === 'marked') btn.classList.add('review-status-marked'); else btn.classList.add('review-status-unanswered');
            btn.onclick = function() { window.jumpToReviewQuestion(i); }; grid.appendChild(btn);
        }); window.updatePaletteHighlight();
    };
    window.updatePaletteHighlight = function() { var btns = document.querySelectorAll('#review-palette-grid .question-nav-btn'); for(var i=0; i<btns.length; i++) btns[i].classList.remove('review-current'); var cur = window.getEl('review-palette-grid').children[window.currentReviewIndex]; if (cur) cur.classList.add('review-current'); };
    window.jumpToReviewQuestion = function(index) { window.loadReviewQuestion(index); }; window.prevReviewQuestion = function() { if (window.currentReviewIndex > 0) window.loadReviewQuestion(window.currentReviewIndex - 1); }; window.nextReviewQuestion = function() { if (window.currentReviewIndex < window.quizData.questions.length - 1) window.loadReviewQuestion(window.currentReviewIndex + 1); }; window.backToResults = function() { window.getEl('review-screen').classList.add('hidden'); window.getEl('results-screen').classList.remove('hidden'); };
    window.openQuestionNav = function() { var grid = window.getEl('question-grid'); grid.innerHTML = ''; window.quizData.questions.forEach(function(_, i) { var cls = 'status-not-answered'; if (window.questionStatus[i] === 'answered') cls = 'status-answered'; if (window.questionStatus[i] === 'marked') cls = 'status-marked'; var btn = document.createElement('button'); btn.textContent = i + 1; btn.className = 'question-nav-btn ' + cls + ' ' + (i === window.currentQuestionIndex ? 'status-current' : '') + ' notranslate'; btn.onclick = function() { window.loadQuestion(i); window.closeQuestionNav(); }; grid.appendChild(btn); }); window.getEl('question-nav-modal').classList.remove('hidden'); };
    window.closeQuestionNav = function() { window.getEl('question-nav-modal').classList.add('hidden'); };

    document.addEventListener('DOMContentLoaded', function() {
        try {
             var themeToggles = [window.getEl('theme-toggle'), window.getEl('review-theme-toggle')];
             themeToggles.forEach(function(t) { if(t) t.addEventListener('click', function() { document.body.classList.toggle('light-mode'); document.body.classList.toggle('dark-mode'); 
                if(document.body.classList.contains('dark-mode')){
                    t.innerHTML = '<i class="fas fa-sun"></i>';
                }else{
                    t.innerHTML = '<i class="fas fa-moon"></i>';
                }
             }) });
        } catch(e) {}
    });
</script>
</body>
</html>
"""

def generate_html(quiz_data: dict, details: dict) -> str:
    processed_content_str = json.dumps(quiz_data, ensure_ascii=False)
    
    final_html = HTML_TEMPLATE.replace('/* QUIZ_DATA_PLACEHOLDER */', processed_content_str)
    
    # Calculate duration
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
