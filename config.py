import os, json, asyncio, logging, time
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from pyrogram.enums import ChatMemberStatus, ParseMode
from pyrogram.errors import RPCError, PeerIdInvalid, ChannelInvalid, ChannelPrivate

# --- LOGGING SETUP ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# =====================================================================
# ROOT-CAUSE PATCH: pyrogram 2.0.106
# =====================================================================
try:
    import pyrogram.utils as _pyro_utils
    _NEW_MIN_CHANNEL_ID = -1007852516352
    _NEW_MIN_CHAT_ID = -999999999999
    if getattr(_pyro_utils, "MIN_CHANNEL_ID", 0) > _NEW_MIN_CHANNEL_ID:
        _pyro_utils.MIN_CHANNEL_ID = _NEW_MIN_CHANNEL_ID
    if getattr(_pyro_utils, "MIN_CHAT_ID", 0) > _NEW_MIN_CHAT_ID:
        _pyro_utils.MIN_CHAT_ID = _NEW_MIN_CHAT_ID
    logger.info(
        f"  Patched pyrogram ID bounds (MIN_CHANNEL_ID={_pyro_utils.MIN_CHANNEL_ID}, "
        f"MIN_CHAT_ID={_pyro_utils.MIN_CHAT_ID})."
    )
except Exception as _patch_err:
    logger.warning(f"  Could not patch pyrogram ID bounds: {_patch_err}")

# --- CONFIGURATION & DEFAULTS ---
DEFAULTS = {"TOKEN": "", "OWNER": 0, "SUPPORT": 0, "MAIN_CH": 0, "LOG_CH": 0, "UPDATE_CH": -1004304703943}

def _safe_int(env_name, default=0):
    raw = os.environ.get(env_name, None)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(str(raw).strip())
    except (ValueError, TypeError):
        logger.warning(f"Env var {env_name}={repr(raw)} is not a valid integer. Using default {default}")
        return default

API_ID = _safe_int("API_ID", 0)
API_HASH = os.environ.get("API_HASH", "") or ""
SESSION_STRING = os.environ.get("SESSION_STRING", "") or ""
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", DEFAULTS["TOKEN"]) or DEFAULTS["TOKEN"]
OWNER_ID = _safe_int("OWNER_ID", DEFAULTS["OWNER"])
SUPPORT_GROUP_ID = _safe_int("SUPPORT_GROUP_ID", DEFAULTS["SUPPORT"])
MANDATORY_CHANNEL_ID = _safe_int("MANDATORY_CHANNEL_ID", DEFAULTS["MAIN_CH"])
LOG_CHANNEL_ID = _safe_int("LOG_CHANNEL_ID", DEFAULTS["LOG_CH"])
BATCH_UPDATE_CHANNEL_ID = _safe_int("BATCH_UPDATE_CHANNEL_ID", -1004304703943)
BATCH_UPDATE_CHANNEL_LINK = os.environ.get("BATCH_UPDATE_CHANNEL_LINK", "https://t.me/c/4304703943")
MONGO_URL = os.environ.get("MONGO_URL", None) or None

if not TELEGRAM_BOT_TOKEN:
    logger.warning("TELEGRAM_BOT_TOKEN is empty! Bot engine will not start until this Space secret is set. Web dashboard will still run.")

MANDATORY_CHANNEL_LINK = os.environ.get("MANDATORY_CHANNEL_LINK", "https://t.me/YourChannel")
VIDEO_TUTORIAL_LINK = os.environ.get("VIDEO_TUTORIAL_LINK", BATCH_UPDATE_CHANNEL_LINK)
DATA_FILE = os.environ.get("DATA_FILE", "bot_data.json")
BOT_USERNAME = os.environ.get("BOT_USERNAME", "YourBot")
WEB_APP_URL = os.environ.get("WEB_APP_URL") or (f"https://{os.environ['SPACE_HOST']}" if "SPACE_HOST" in os.environ else (os.environ.get("RENDER_EXTERNAL_URL") or "https://itsh4r01-user-manager.hf.space"))

DEFAULT_CATEGORIES = ["Civil Engg.", "Electrical Engg.", "Mechanical Engg.", "Electronics Engg.", "CSE", "Competition (Math, Reas, GK/GS)", "Skill Improvement", "Other Batches"]

DEFAULT_VACANCIES = [
    {
        "id": "vac_upsc_cse_2026",
        "title": "UPSC Civil Services (IAS/IPS) & IFS Prelims 2026",
        "org": "Union Public Service Commission (UPSC)",
        "category": "UPSC",
        "total_posts": "1,056+ Posts",
        "eligibility": "Graduation in any stream from recognized university",
        "age_limit": "21 - 32 Years (OBC +3, SC/ST +5)",
        "last_date": "04 March 2026",
        "exam_date": "24 May 2026",
        "status": "active",
        "notif_link": "https://upsc.gov.in/examinations/active-exams",
        "apply_link": "https://upsconline.nic.in"
    },
    {
        "id": "vac_ssc_cgl_2026",
        "title": "SSC CGL 2026 (Combined Graduate Level) Tier-1",
        "org": "Staff Selection Commission (SSC)",
        "category": "SSC",
        "total_posts": "14,800+ Posts (ASO, Inspector, Auditor)",
        "eligibility": "Bachelor's Degree in any discipline",
        "age_limit": "18 - 30/32 Years as per post",
        "last_date": "20 March 2026",
        "exam_date": "June / July 2026",
        "status": "active",
        "notif_link": "https://ssc.gov.in/notices",
        "apply_link": "https://ssc.gov.in"
    },
    {
        "id": "vac_rrb_je_ntpc_2026",
        "title": "RRB Junior Engineer & NTPC Graduate/Undergraduate 2026",
        "org": "Railway Recruitment Board (RRB)",
        "category": "Railways",
        "total_posts": "11,558 Posts",
        "eligibility": "Diploma / B.Tech (JE) or Any Degree / 12th (NTPC)",
        "age_limit": "18 - 36 Years",
        "last_date": "15 April 2026",
        "exam_date": "August 2026",
        "status": "active",
        "notif_link": "https://indianrailways.gov.in",
        "apply_link": "https://www.rrbapply.gov.in"
    },
    {
        "id": "vac_sbi_po_clerk_2026",
        "title": "SBI & IBPS Probationary Officers (PO) & Clerks 2026",
        "org": "State Bank of India & IBPS",
        "category": "Banking",
        "total_posts": "9,800+ Posts",
        "eligibility": "Any Graduate from a recognized University",
        "age_limit": "20 - 30 Years",
        "last_date": "28 March 2026",
        "exam_date": "May / June 2026",
        "status": "active",
        "notif_link": "https://sbi.co.in/careers",
        "apply_link": "https://ibps.in"
    },
    {
        "id": "vac_neet_ug_2026",
        "title": "NTA NEET UG 2026 Medical Entrance (MBBS/BDS)",
        "org": "National Testing Agency (NTA)",
        "category": "Medical",
        "total_posts": "1,08,000+ MBBS & BDS Seats All India",
        "eligibility": "10+2 with Physics, Chemistry, Biology & English (Min 50%)",
        "age_limit": "Min 17 Years (No upper age limit)",
        "last_date": "10 March 2026",
        "exam_date": "03 May 2026",
        "status": "active",
        "notif_link": "https://neet.nta.nic.in",
        "apply_link": "https://neet.nta.nic.in"
    },
    {
        "id": "vac_nda_cds_2026",
        "title": "UPSC NDA & NA (I) / CDS (I) Defence Academy 2026",
        "org": "Union Public Service Commission (Defence)",
        "category": "Defence",
        "total_posts": "850+ Officer Cadet Vacancies (Army, Navy, Air Force)",
        "eligibility": "12th Pass (PCM for Navy/AF) for NDA / Degree for CDS",
        "age_limit": "16.5 - 19.5 (NDA) / 19 - 24 (CDS)",
        "last_date": "18 February 2026",
        "exam_date": "19 April 2026",
        "status": "active",
        "notif_link": "https://upsc.gov.in",
        "apply_link": "https://upsconline.nic.in"
    },
    {
        "id": "vac_ctet_teaching_2026",
        "title": "CBSE CTET 2026 (Central Teacher Eligibility Test)",
        "org": "Central Board of Secondary Education (CBSE)",
        "category": "Teaching",
        "total_posts": "National Eligibility for PRT, TGT & PGT Teaching Posts",
        "eligibility": "D.El.Ed / B.Ed / Graduation with min 50% marks",
        "age_limit": "No upper age limit",
        "last_date": "25 March 2026",
        "exam_date": "July 2026",
        "status": "active",
        "notif_link": "https://ctet.nic.in",
        "apply_link": "https://ctet.nic.in"
    }
]

DEFAULT_CURRENT_AFFAIRS = [
    {
        "id": "ca_1",
        "date": "09 Nov 2026",
        "topic": "National & Governance",
        "title": "Cabinet approves PM GatiShakti Multi-Modal Connectivity Masterplan 2.0",
        "snippet": "Union Cabinet has approved Phase 2 of PM GatiShakti National Master Plan to integrate high-speed freight corridors, inland waterways, and digital cargo hubs across 28 states, expediting supply-chain efficiency."
    },
    {
        "id": "ca_2",
        "date": "08 Nov 2026",
        "topic": "Economy & RBI",
        "title": "RBI Monetary Policy Committee maintains Repo Rate at 6.50% with 'Neutral' Stance",
        "snippet": "The Reserve Bank of India MPC voted unanimously to keep the policy repo rate unchanged at 6.50% while emphasizing headline inflation convergence to the 4.0% median target."
    },
    {
        "id": "ca_3",
        "date": "07 Nov 2026",
        "topic": "Science & Space Technology",
        "title": "ISRO successfully conducts Hot-Test of Semi-Cryogenic Pre-Burner for LVM3",
        "snippet": "ISRO's Propulsion Complex (IPRC) at Mahendragiri achieved successful hot-testing of the Semi-Cryogenic Engine pre-burner, augmenting payload delivery to Geo-Transfer Orbit (GTO) up to 6 tonnes."
    },
    {
        "id": "ca_4",
        "date": "06 Nov 2026",
        "topic": "Environment & Biodiversity",
        "title": "India adds two new Ramsar Wetland Sites, tally reaches 87",
        "snippet": "The Ministry of Environment, Forest and Climate Change notified two new wetland reserves in Gujarat and Odisha as Ramsar Sites of International Importance, taking India's total Ramsar count to 87."
    },
    {
        "id": "ca_5",
        "date": "05 Nov 2026",
        "topic": "Defence & Security",
        "title": "DRDO test-fires Indigenous Long-Range Glide Bomb (Gaurav) from Su-30MKI",
        "snippet": "The Defence Research and Development Organisation (DRDO) successfully completed flight trials of the 1,000 kg class Long Range Glide Bomb 'Gaurav' equipped with indigenous satellite navigation guidance."
    }
]

DEFAULT_AD_CONFIG = {
    "ads_enabled": True,
    "adsgram_block_id": "",
    "rewarded_coins": 2.0,
    "sponsor_banner_enabled": True,
    "sponsor_banner_tag": "⚡ Official Partner Channel",
    "sponsor_banner_title": "🚀 Join Official H4R Exam Channel for Free PDF Batches & Alerts",
    "sponsor_banner_desc": "Get instant daily PDFs, class notes & live job notifications directly in Telegram.",
    "sponsor_banner_btn": "Join Channel Free ↗",
    "sponsor_banner_link": BATCH_UPDATE_CHANNEL_LINK or "https://t.me/H4R_Contact_bot"
}

DEFAULT_RECOMMENDED_RESOURCES = [
    {
        "id": "book_pinnacle_math",
        "title": "Pinnacle SSC Maths 6800+ TCS MCQ (Bilingual)",
        "author": "Pinnacle Publication",
        "category": "SSC & Railways",
        "badge": "🔥 Most Recommended by Toppers",
        "rating": "4.8 ★",
        "highlight": "Chapter-wise & Type-wise TCS questions with short video solutions & tricks.",
        "link": "https://www.amazon.in/dp/B0CL5N5KGB?tag=h4r01-21",
        "btn_text": "View on Amazon ↗"
    },
    {
        "id": "book_blackbook_eng",
        "title": "Black Book of English Vocabulary",
        "author": "Nikhil Gupta",
        "category": "SSC, CDS & Banking",
        "badge": "⭐ 100% PYQ Vocab Coverage",
        "rating": "4.9 ★",
        "highlight": "One Word Substitutions, Idioms, Synonyms & Antonyms asked in all exams till 2025.",
        "link": "https://www.amazon.in/dp/9355938833?tag=h4r01-21",
        "btn_text": "View on Amazon ↗"
    },
    {
        "id": "pass_testbook_pro",
        "title": "Testbook Pass Pro (All Exams Mock Tests)",
        "author": "Testbook Live Tests",
        "category": "All Competitive Exams",
        "badge": "🎯 70,000+ Mock Tests & PYQs",
        "rating": "4.8 ★",
        "highlight": "Unlimited re-attempt, exact TCS exam screen interface & All-India Rank analytics.",
        "link": "https://testbook.com/invite/H4RPRO",
        "btn_text": "Claim Student Discount ↗"
    },
    {
        "id": "book_laxmikanth_polity",
        "title": "Indian Polity by M. Laxmikanth (7th Edition)",
        "author": "M. Laxmikanth (McGraw Hill)",
        "category": "UPSC & State PSCs",
        "badge": "🏛️ The Bible of Indian Polity",
        "rating": "4.9 ★",
        "highlight": "Covers complete constitutional articles, Supreme Court landmark cases & DPSP.",
        "link": "https://www.amazon.in/dp/9355325858?tag=h4r01-21",
        "btn_text": "View on Amazon ↗"
    },
    {
        "id": "book_speedy_railway",
        "title": "Speedy Railway Samanya Vigyan (General Science)",
        "author": "Speedy Publications",
        "category": "Railways RRB NTPC / Group D",
        "badge": "🚂 Best for RRB CBT 1 & 2",
        "rating": "4.7 ★",
        "highlight": "Direct one-liner science questions asked across 25 years of railway recruitment.",
        "link": "https://www.amazon.in/dp/B09XYZ1234?tag=h4r01-21",
        "btn_text": "View on Amazon ↗"
    }
]

DEFAULT_MOCK_TESTS = [
    {
        "id": "test_ssc_cgl_ga",
        "title": "SSC CGL / CHSL Tier-1 General Awareness & Science Mock Test",
        "category": "SSC",
        "duration_minutes": 15,
        "total_marks": 20,
        "positive_mark": 2.0,
        "negative_mark": 0.50,
        "level": "Intermediate",
        "instructions": "Each correct answer carries +2.0 marks. Negative marking of 0.50 marks for each incorrect response. Unattempted questions carry zero penalty.",
        "questions": [
            {
                "id": 1,
                "topic": "Indian Constitution",
                "question": "Which Article of the Constitution of India provides for the creation of an all-India Public Service Commission for the Union and a Public Service Commission for each State?",
                "options": ["Article 312", "Article 315", "Article 324", "Article 326"],
                "answer": 1,
                "explanation": "Article 315 provides for Public Service Commissions for the Union and for the States. Article 312 relates to All India Services."
            },
            {
                "id": 2,
                "topic": "Indian Economy",
                "question": "Which institution releases the Multi-dimensional Poverty Index (MPI) for India in collaboration with UNDP and OPHI?",
                "options": ["Reserve Bank of India", "NITI Aayog", "Ministry of Finance", "Central Statistics Office (CSO)"],
                "answer": 1,
                "explanation": "NITI Aayog publishes the National Multidimensional Poverty Index (MPI) based on NFHS surveys measuring health, education and standard of living."
            },
            {
                "id": 3,
                "topic": "Science - Chemistry",
                "question": "What is the common chemical name of Plaster of Paris (PoP)?",
                "options": ["Calcium Carbonate", "Calcium Sulphate Dihydrate", "Calcium Sulphate Hemihydrate", "Sodium Hydrogen Carbonate"],
                "answer": 2,
                "explanation": "Plaster of Paris is Calcium Sulphate Hemihydrate (CaSO4·½H2O), produced by heating Gypsum (CaSO4·2H2O) at 373 K."
            },
            {
                "id": 4,
                "topic": "History - Delhi Sultanate",
                "question": "Which ruler of Delhi Sultanate introduced the 'Token Currency' system made of copper and brass in 1329–1330 CE?",
                "options": ["Alauddin Khilji", "Muhammad bin Tughlaq", "Feroz Shah Tughlaq", "Balban"],
                "answer": 1,
                "explanation": "Muhammad bin Tughlaq introduced token bronze/copper currency equivalent to silver Tanka, though widespread counterfeiting led to its recall."
            },
            {
                "id": 5,
                "topic": "Geography",
                "question": "Which river originates from the Mansarovar Lake region in Tibet and enters India through Arunachal Pradesh under the name 'Siang'?",
                "options": ["Indus River", "Brahmaputra River", "Sutlej River", "Mahanadi River"],
                "answer": 1,
                "explanation": "The Yarlung Tsangpo in Tibet enters Arunachal Pradesh as Siang / Dihang, joining the Lohit and Dibang to become the Brahmaputra in Assam."
            },
            {
                "id": 6,
                "topic": "Science - Biology",
                "question": "Which organelle is universally referred to as the 'Suicidal Bag' of a cell due to its hydrolytic digestive enzymes?",
                "options": ["Ribosome", "Lysosome", "Centrosome", "Endoplasmic Reticulum"],
                "answer": 1,
                "explanation": "Lysosomes contain acidic hydrolases; when damaged, they rupture and digest their own cell, earning the title 'suicidal bags'."
            },
            {
                "id": 7,
                "topic": "Art & Culture",
                "question": "The classical dance form 'Sattriya' originated in which Indian State under the bhakti movement of Srimanta Sankardev?",
                "options": ["Manipur", "Odisha", "Assam", "Kerala"],
                "answer": 2,
                "explanation": "Sattriya was originated in the 15th century by Vaishnava saint Srimanta Sankardev in the Sattras (monasteries) of Assam."
            },
            {
                "id": 8,
                "topic": "Indian Geography",
                "question": "The southernmost point of Indian territory, 'Indira Point' (Pygmalion Point), is located on which island?",
                "options": ["North Andaman", "Little Andaman", "Great Nicobar", "Car Nicobar"],
                "answer": 2,
                "explanation": "Indira Point is situated at 6°45' N on Great Nicobar Island, facing the Great Channel."
            },
            {
                "id": 9,
                "topic": "Science - Physics",
                "question": "A body floats in water with 1/3rd of its volume above the surface. What is the relative density of the body?",
                "options": ["0.33", "0.67", "1.00", "1.33"],
                "answer": 1,
                "explanation": "By Archimedes' principle, fraction submerged = (1 - 1/3) = 2/3 ≈ 0.67. The relative density is equal to the fraction of volume submerged (0.67)."
            },
            {
                "id": 10,
                "topic": "Current Affairs & Sports",
                "question": "The 2026 Commonwealth Games and major international Olympic qualification cycles follow the WADA anti-doping code. Where is the headquarters of WADA located?",
                "options": ["Lausanne, Switzerland", "Montreal, Canada", "Vienna, Austria", "Geneva, Switzerland"],
                "answer": 1,
                "explanation": "The World Anti-Doping Agency (WADA) has its headquarters in Montreal, Canada."
            }
        ]
    },
    {
        "id": "test_rrb_ntpc_science",
        "title": "RRB NTPC & Group D General Science & Math Power Test",
        "category": "Railways",
        "duration_minutes": 15,
        "total_marks": 10,
        "positive_mark": 1.0,
        "negative_mark": 0.33,
        "level": "High-Yield",
        "instructions": "Exam pattern: +1.0 for each correct answer, -0.33 penalty for each incorrect attempt.",
        "questions": [
            {
                "id": 1,
                "topic": "Physics - Optics",
                "question": "Which type of mirror is utilized as a rearview mirror in motor vehicles for providing a wide field of view?",
                "options": ["Concave Mirror", "Convex Mirror", "Plane Mirror", "Parabolic Mirror"],
                "answer": 1,
                "explanation": "Convex mirrors produce virtual, erect, and diminished images, offering an expansive field of view for vehicle rear inspection."
            },
            {
                "id": 2,
                "topic": "Chemistry - Metals",
                "question": "Which non-metal is in liquid state at standard room temperature and pressure?",
                "options": ["Mercury", "Bromine", "Phosphorus", "Chlorine"],
                "answer": 1,
                "explanation": "Bromine (Br) is the only liquid non-metal at standard room temperature. Mercury is a liquid metal."
            },
            {
                "id": 3,
                "topic": "Railways Math",
                "question": "A train running at 72 km/h crosses a 200m platform in 20 seconds. What is the length of the train?",
                "options": ["150 m", "200 m", "250 m", "300 m"],
                "answer": 1,
                "explanation": "Speed = 72 × (5/18) = 20 m/s. Total distance = Speed × Time = 20 × 20 = 400m. Length of train = 400 - 200 = 200m."
            },
            {
                "id": 4,
                "topic": "Biology - Human Body",
                "question": "Which endocrine gland in the human body is termed the 'Master Gland' of the endocrine system?",
                "options": ["Thyroid Gland", "Adrenal Gland", "Pituitary Gland", "Pancreas"],
                "answer": 2,
                "explanation": "The pituitary gland produces hormones (TSH, ACTH, GH, LH) regulating other endocrine glands, functioning as the master gland."
            },
            {
                "id": 5,
                "topic": "Physics - Energy",
                "question": "What is the commercial unit of electrical energy commonly denoted as '1 Unit' in electricity meters?",
                "options": ["1 Joule", "1 Watt-hour", "1 Kilowatt-hour (kWh)", "1 Megawatt"],
                "answer": 2,
                "explanation": "1 commercial unit = 1 kWh = 1000 Watts × 3600 seconds = 3.6 × 10^6 Joules."
            },
            {
                "id": 6,
                "topic": "Chemistry - Acids & Bases",
                "question": "Which organic acid is naturally present in sour milk or curd?",
                "options": ["Citric Acid", "Lactic Acid", "Oxalic Acid", "Tartaric Acid"],
                "answer": 1,
                "explanation": "Lactic acid is produced through the fermentation of lactose milk sugar by Lactobacillus bacteria."
            },
            {
                "id": 7,
                "topic": "Mathematics - Profit & Loss",
                "question": "By selling an article for ₹720, a shopkeeper incurs a 20% loss. At what price must it be sold to gain 20% profit?",
                "options": ["₹880", "₹960", "₹1,000", "₹1,080"],
                "answer": 3,
                "explanation": "CP = 720 / 0.8 = ₹900. Required SP with 20% gain = 900 × 1.2 = ₹1,080."
            },
            {
                "id": 8,
                "topic": "Physics - Sound",
                "question": "What is the minimum distance between the sound source and reflecting surface required to hear a distinct echo in air (at 22°C)?",
                "options": ["11.2 metres", "17.2 metres", "25.0 metres", "34.0 metres"],
                "answer": 1,
                "explanation": "Human ear persistence of hearing is 0.1s. Speed of sound in air ≈ 344 m/s. Distance = (344 × 0.1)/2 = 17.2 metres."
            },
            {
                "id": 9,
                "topic": "Biology - Vitamins",
                "question": "Night blindness (Nyctalopia) and Xerophthalmia are deficiency disorders caused by lack of which vitamin?",
                "options": ["Vitamin A (Retinol)", "Vitamin B1 (Thiamine)", "Vitamin C (Ascorbic Acid)", "Vitamin D (Calciferol)"],
                "answer": 0,
                "explanation": "Vitamin A (Retinol) is essential for rhodopsin synthesis in rod cells of retina; its deficiency causes night blindness."
            },
            {
                "id": 10,
                "topic": "Mathematics - Ratios",
                "question": "The ratio of two numbers is 3:4 and their HCF is 4. What is the LCM of these two numbers?",
                "options": ["24", "36", "48", "60"],
                "answer": 2,
                "explanation": "The numbers are 3×4 = 12 and 4×4 = 16. LCM(12, 16) = 48 (or 3 × 4 × HCF = 3 × 4 × 4 = 48)."
            }
        ]
    },
    {
        "id": "test_upsc_prelims_gs",
        "title": "UPSC Civil Services Prelims GS-1 High-Yield Practice Mock",
        "category": "UPSC",
        "duration_minutes": 20,
        "total_marks": 20,
        "positive_mark": 2.0,
        "negative_mark": 0.66,
        "level": "UPSC Standard",
        "instructions": "Standard UPSC format: +2.0 Marks for each correct response, -0.66 deduction for each wrong answer.",
        "questions": [
            {
                "id": 1,
                "topic": "Indian Polity",
                "question": "With reference to the Parliamentary system in India, which of the following is/are correct regarding the 'Motion of Thanks' on President's address?\n1. It is discussed and voted upon in both Houses of Parliament.\n2. The defeat of the Motion of Thanks in Lok Sabha amounts to the defeat of the Government.\nSelect the correct answer using the code given below:",
                "options": ["1 only", "2 only", "Both 1 and 2", "Neither 1 nor 2"],
                "answer": 2,
                "explanation": "Both statements are correct. The Motion of Thanks is moved in both Houses; its defeat in the Lok Sabha signifies loss of majority and causes government resignation."
            },
            {
                "id": 2,
                "topic": "Environment & Ecology",
                "question": "Consider the following statements regarding the 'Global Biodiversity Framework (Kunming-Montreal GBF)':\n1. It aims to protect at least 30% of planet's land, coastal areas and inland waters by 2030 (30x30 Target).\n2. It was adopted under the UN Framework Convention on Climate Change (UNFCCC).\nWhich of the statements given above is/are correct?",
                "options": ["1 only", "2 only", "Both 1 and 2", "Neither 1 nor 2"],
                "answer": 0,
                "explanation": "Statement 1 is correct (30x30 target). Statement 2 is incorrect as it was adopted under the UN Convention on Biological Diversity (CBD) COP15, not UNFCCC."
            },
            {
                "id": 3,
                "topic": "Economic Geography",
                "question": "The 'Kavach' automatic train protection (ATP) system implemented by Indian Railways operates on which principle technology?",
                "options": ["Satellite laser radar (LiDAR)", "Radio Frequency Identification (RFID) and ultra-high frequency radio telemetry", "Ultrasonic rail Doppler sensors", "Acoustic optical fibre sensing"],
                "answer": 1,
                "explanation": "Kavach is an indigenously developed SIL-4 electronic system using RFID readers on tracks and UHF/VHF radio telemetry between locomotive and station."
            },
            {
                "id": 4,
                "topic": "Ancient & Medieval History",
                "question": "With reference to ancient Indian temple architecture, the terms 'Garbhagriha', 'Mandapa', 'Shikhara' and 'Amalaka' are primarily characteristic features of:",
                "options": ["Dravida Style only", "Nagara Style of North India", "Vesara Style exclusively", "Cave Rock-cut Architecture"],
                "answer": 1,
                "explanation": "The Nagara style of North Indian temple architecture is distinguished by the Amalaka (ribbed stone disc) crowning the curvilinear Shikhara over the Garbhagriha."
            },
            {
                "id": 5,
                "topic": "Modern History",
                "question": "The 'Cabinet Mission' of 1946 rejected the demand for a sovereign Pakistan and proposed:",
                "options": ["Immediate Partition into two dominions", "A three-tier federation with weak centre possessing defence, foreign affairs and communications", "Direct transfer of power to the Indian National Army", "Unitary unitary governance without provincial groupings"],
                "answer": 1,
                "explanation": "The Cabinet Mission (Pethick-Lawrence, Cripps, Alexander) proposed a loose 3-tier federation grouped into Sections A, B and C with residual powers in provinces."
            },
            {
                "id": 6,
                "topic": "Science & Space",
                "question": "In the context of astronomy and deep space exploration, what does the term 'Lagrange Point 1 (L1)' signify, as utilized in ISRO's Aditya-L1 solar mission?",
                "options": ["A point of zero gravitational acceleration in inter-galactic void", "An equilibrium position where gravitational forces of Sun and Earth produce an unobstructed solar view without occultation", "The nearest orbital approach of Moon to Earth", "A point of maximum geomagnetic solar flare impact"],
                "answer": 1,
                "explanation": "Lagrange Point 1 (L1), located 1.5 million km from Earth towards the Sun, provides an unhindered, continuous view of the Sun without eclipses."
            },
            {
                "id": 7,
                "topic": "Economy & Banking",
                "question": "Which of the following constitutes the largest component of India's Foreign Exchange Reserves managed by the RBI?",
                "options": ["Gold Holdings", "Foreign Currency Assets (FCA)", "Special Drawing Rights (SDRs)", "Reserve Tranche Position (RTP) in IMF"],
                "answer": 1,
                "explanation": "Foreign Currency Assets (FCA) held in major currencies (USD, EUR, GBP, JPY) comprise ~85% of total Indian forex reserves."
            },
            {
                "id": 8,
                "topic": "Indian Polity",
                "question": "Which Constitutional Article guarantees that 'No person shall be prosecuted and punished for the same offence more than once' (Doctrine against Double Jeopardy)?",
                "options": ["Article 20(1)", "Article 20(2)", "Article 20(3)", "Article 21"],
                "answer": 1,
                "explanation": "Article 20(2) incorporates the rule against double jeopardy: no person shall be prosecuted and punished for the same offence more than once."
            },
            {
                "id": 9,
                "topic": "Environment & Wetlands",
                "question": "The 'Montreux Record' is a register under the Ramsar Convention. What does it maintain?",
                "options": ["List of newly discovered wetland fish species", "Wetland sites where changes in ecological character have occurred, are occurring, or are likely to occur", "Commercial trade data of wetland peat minerals", "Global carbon tax rates on mangrove wetlands"],
                "answer": 1,
                "explanation": "Montreux Record lists Ramsar wetland sites facing acute ecological disturbance due to technological development, pollution or human interference (e.g. Keoladeo and Loktak in India)."
            },
            {
                "id": 10,
                "topic": "Geography & Oceanography",
                "question": "Which of the following ocean currents is a Cold Ocean Current in the Atlantic Ocean?",
                "options": ["Gulf Stream", "Benguela Current", "Brazil Current", "North Atlantic Drift"],
                "answer": 1,
                "explanation": "The Benguela Current is a cold ocean current that flows northward along the west coast of southern Africa into the South Atlantic Ocean."
            }
        ]
    },
    {
        "id": "test_daily_ca_sprint",
        "title": "Daily 9 AM High-Yield Current Affairs Sprint",
        "category": "Daily Sprint",
        "duration_minutes": 10,
        "total_marks": 10,
        "positive_mark": 1.0,
        "negative_mark": 0.25,
        "level": "Daily Routine",
        "instructions": "Daily Morning 10-Question Sprint. Score 8+ to earn bonus study coins and maintain your focus streak!",
        "questions": [
            {
                "id": 1,
                "topic": "National Affairs",
                "question": "Under PM GatiShakti National Master Plan 2.0, how many multimodal cargo hubs and dedicated freight terminals are targeted across India?",
                "options": ["50 Hubs", "100+ Multimodal Hubs", "500 Hubs", "25 Hubs"],
                "answer": 1,
                "explanation": "Phase 2 emphasizes setting up 100+ Multimodal cargo logistic terminals to reduce national logistics cost to under 8% of GDP."
            },
            {
                "id": 2,
                "topic": "Monetary Policy",
                "question": "What is the primary operational target of the RBI Monetary Policy Committee (MPC) under the Flexible Inflation Targeting (FIT) framework?",
                "options": ["GDP growth rate of 8.0%", "CPI inflation target of 4.0% with +/- 2% tolerance band (2% to 6%)", "Fiscal deficit reduction to 3.0%", "Fixed currency exchange rate to USD"],
                "answer": 1,
                "explanation": "Section 45ZA of RBI Act mandates maintaining CPI inflation at 4% with a tolerance band of +/- 2% (i.e. 2% to 6%)."
            },
            {
                "id": 3,
                "topic": "Defence Tech",
                "question": "What is the operational standoff range of the DRDO-developed 'Gaurav' Long-Range Glide Bomb?",
                "options": ["20 to 30 km", "Nearly 100 km", "Over 500 km", "1000 km"],
                "answer": 1,
                "explanation": "Gaurav is a winged glide bomb capable of steering towards targets at standoff distances around 100 km using inertial navigation assisted by GPS/NavIC."
            },
            {
                "id": 4,
                "topic": "Environment",
                "question": "Which international treaty celebrated World Wetlands Day on 2nd February and oversees the Ramsar List of Wetlands of International Importance?",
                "options": ["Bonn Convention (CMS)", "Ramsar Convention (signed in Ramsar, Iran, 1971)", "Basel Convention", "Stockholm Convention"],
                "answer": 1,
                "explanation": "The Ramsar Convention was signed on 2 February 1971 in the Iranian city of Ramsar on the shores of the Caspian Sea."
            },
            {
                "id": 5,
                "topic": "Space Exploration",
                "question": "ISRO's LVM3 (Launch Vehicle Mark 3), previously designated GSLV Mk III, is a 3-stage heavy-lift vehicle. What is its second stage?",
                "options": ["Solid S200 Boosters", "Liquid L110 Vikas Engine Stage", "Cryogenic C25 Stage", "Semi-Cryogenic SC120 Stage"],
                "answer": 1,
                "explanation": "LVM3 consists of two S200 solid rocket boosters, an L110 core liquid stage (twin Vikas engines), and a C25 cryogenic upper stage."
            },
            {
                "id": 6,
                "topic": "Economy",
                "question": "Which agency is responsible for calculating and publishing the Index of Eight Core Industries (ICI) in India?",
                "options": ["Office of Economic Adviser (DPIIT), Ministry of Commerce", "NITI Aayog", "Ministry of Labour", "National Sample Survey Office (NSSO)"],
                "answer": 0,
                "explanation": "The Office of Economic Adviser under DPIIT, Ministry of Commerce and Industry releases the monthly Index of Eight Core Industries."
            },
            {
                "id": 7,
                "topic": "Judiciary & Rights",
                "question": "In the landmark 'K.S. Puttaswamy v. Union of India (2017)' judgment, the Supreme Court declared which right as a Fundamental Right under Article 21?",
                "options": ["Right to Free Internet", "Right to Privacy", "Right to Shelter", "Right to Free Legal Aid"],
                "answer": 1,
                "explanation": "A unanimous 9-judge constitutional bench ruled that the Right to Privacy is an intrinsic part of the Right to Life and Personal Liberty under Article 21."
            },
            {
                "id": 8,
                "topic": "International Groups",
                "question": "Which country recently officially joined the BRICS grouping as new full member along with Egypt, Ethiopia, Iran and UAE?",
                "options": ["Saudi Arabia", "Indonesia", "Argentina", "Singapore"],
                "answer": 0,
                "explanation": "Saudi Arabia, along with Egypt, Ethiopia, Iran, and UAE, were invited to join BRICS in its historic expansion."
            },
            {
                "id": 9,
                "topic": "Renewable Energy",
                "question": "Under the PM Surya Ghar: Muft Bijli Yojana, what is the maximum monthly free solar electricity targeted for 1 crore households?",
                "options": ["100 Units", "200 Units", "300 Units", "500 Units"],
                "answer": 2,
                "explanation": "PM Surya Ghar provides up to 300 units of free solar electricity monthly to 1 crore households through rooftop solar subsidies."
            },
            {
                "id": 10,
                "topic": "Cybersecurity & AI",
                "question": "What is the name of India's national AI computing infrastructure initiative approved with a ₹10,372 crore outlay?",
                "options": ["IndiaAI Mission", "Digital India AI", "Bharat GPT", "Vikshit AI 2047"],
                "answer": 0,
                "explanation": "The Union Cabinet approved the comprehensive IndiaAI Mission with a budget of ₹10,372 crore to establish 10,000+ GPU supercomputing capacity."
            }
        ]
    }
]

# --- DATABASE & MEMORY ---
DB = {
    "ADMIN_IDS": [], "FREE_CHANNELS": {}, "PAID_CHANNELS": {}, "SPECIAL_CHANNELS": {}, "ALL_CHATS": {},
    "USER_DATA": {}, "BLOCKED_USERS": [], "USER_TOPICS": {}, "PENDING_REQUESTS": {},
    "LINK_MAP": {}, "CUSTOM_WELCOMES": {}, "NEW_USERS_ALLOWED": True, 
    "FREE_LOCKED": False, "PAID_LOCKED": False, "TEST_BOT_LOCKED": False, 
    "SCHEDULED_DELETES": [], "TEST_BOT_LINK": "", "BATCH_CATEGORIES": {},
    "CATEGORIES": DEFAULT_CATEGORIES.copy(), "MAINTENANCE_MODE": True,
    "TESTER_IDS": [],
    "BATCH_COINS": {}, "BATCH_REQUESTS": {}, "BATCH_REQUEST_STATUS": {}, "VOUCHERS": {},
    "BATCH_PRICES": {},
    "BATCH_REQUEST_NOTE": "🙏 **Important Note from Admin:**\nMain abhi regular active nahi reh pata hu isliye batch upload/add hone me thoda waqt lag sakta hai. Aap bilkul chinta na karein aur thoda dhairya (patience) rakhein. Jaise hi yeh batch bot me add hoga, bot aapko direct notification bhej dega! Tab tak ke liye dhanyawad.",
    "USERBOT_SESSION": None, "USERBOT_PHONE": None,
    "VIP_MATERIALS_LINK": None, "VIP_STICKER_ID": None, "VIP_STICKER_TYPE": None,
    "BATCH_UPDATE_CHANNEL_ID": BATCH_UPDATE_CHANNEL_ID,
    "BATCH_UPDATE_CHANNEL_LINK": BATCH_UPDATE_CHANNEL_LINK,
    "STUDY_TASKS": {},
    "EXAM_VACANCIES": DEFAULT_VACANCIES.copy(),
    "CURRENT_AFFAIRS": DEFAULT_CURRENT_AFFAIRS.copy(),
    "MOCK_TESTS": DEFAULT_MOCK_TESTS.copy(),
    "MOCK_TEST_RESULTS": {},
    "AD_CONFIG": DEFAULT_AD_CONFIG.copy(),
    "GEMINI_API_KEY": os.environ.get("GEMINI_API_KEY", "")
}

class BoundedDict(dict):
    """Memory-safe LRU dict that automatically purges oldest entries when exceeding maxlen."""
    def __init__(self, maxlen=5000, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.maxlen = maxlen
    def __setitem__(self, key, value):
        if len(self) >= self.maxlen and key not in self:
            try:
                oldest_key = next(iter(self))
                del self[oldest_key]
            except Exception:
                pass
        super().__setitem__(key, value)

MESSAGE_MAP = BoundedDict(maxlen=5000) 
ADMIN_WIZARD = {} 
BROADCAST_STATE = {} 
TOPIC_CREATION_LOCK = set()
SPAM_CACHE = {} 
data_lock = asyncio.Lock()
mongo_client = mongo_collection = None

# --- GLOBAL FLOODWAIT STATE ---
FLOOD_WAIT_UNTIL = 0

def get_flood_wait_status():
    remaining = FLOOD_WAIT_UNTIL - time.time()
    if remaining > 0:
        return True, int(remaining) + 1
    return False, 0

# --- MONGODB SETUP (WITH TIMEOUT SAFETY) ---
if MONGO_URL and os.environ.get("SKIP_MONGO") != "1":
    try:
        logger.info("  Connecting to MongoDB Atlas...")
        from pymongo import MongoClient
        import certifi
        mongo_client = MongoClient(
            MONGO_URL, 
            tlsCAFile=certifi.where(),
            serverSelectionTimeoutMS=5000,
            socketTimeoutMS=5000,
            connectTimeoutMS=5000
        )
        mongo_db = mongo_client.get_database("telegram_bot_db")
        mongo_collection = mongo_db.get_collection("bot_settings")
        logger.info("  Connected to MongoDB Atlas Successfully!")
    except Exception as e:
        logger.error(f"  MongoDB Connection Failed: {e}")
        MONGO_URL = None

# --- DATABASE FUNCTIONS ---
def load_data():
    global DB
    logger.info("  Loading Data...")
    keys_to_load = [
        "LINK_MAP", "NEW_USERS_ALLOWED", "FREE_LOCKED", "PAID_LOCKED", "TEST_BOT_LOCKED", 
        "SCHEDULED_DELETES", "TEST_BOT_LINK", "MAINTENANCE_MODE", "CATEGORIES", "BATCH_CATEGORIES", 
        "USERBOT_SESSION", "USERBOT_PHONE",
        "VIP_MATERIALS_LINK", "VIP_STICKER_ID", "VIP_STICKER_TYPE", "BATCH_COINS",
        "BATCH_REQUESTS", "BATCH_REQUEST_STATUS", "VOUCHERS", "BATCH_PRICES", "BATCH_REQUEST_NOTE",
        "BATCH_UPDATE_CHANNEL_ID", "BATCH_UPDATE_CHANNEL_LINK", "EXAM_VACANCIES", "CURRENT_AFFAIRS",
        "TESTER_IDS", "MOCK_TESTS", "MOCK_TEST_RESULTS", "AD_CONFIG",
    ]
    
    if MONGO_URL and mongo_collection is not None:
        try:
            data = mongo_collection.find_one({"_id": "main_settings"})
            if data and "data" in data:
                loaded = data["data"]
                def _get_val(d, k):
                    if k in d: return d[k]
                    if k.lower() in d: return d[k.lower()]
                    return None
                    
                val_admin = _get_val(loaded, "ADMIN_IDS")
                if val_admin is not None: DB["ADMIN_IDS"] = [int(x) for x in val_admin if str(x).isdigit()]
                
                val_blocked = _get_val(loaded, "BLOCKED_USERS")
                if val_blocked is not None: DB["BLOCKED_USERS"] = val_blocked
                
                for k in keys_to_load:
                    val = _get_val(loaded, k)
                    if val is not None: DB[k] = val
                
                for k in ["CUSTOM_WELCOMES", "FREE_CHANNELS", "PAID_CHANNELS", "SPECIAL_CHANNELS", "ALL_CHATS", "USER_TOPICS", "USER_DATA", "PENDING_REQUESTS"]:
                    val = _get_val(loaded, k)
                    if val is not None: 
                        if k == "USER_TOPICS":
                            sanitized = {}
                            for i, v in val.items():
                                i_key = int(i) if str(i).lstrip('-').isdigit() else i
                                if isinstance(v, dict):
                                    sanitized[i_key] = v.get("topic_id") or v.get("message_thread_id") or 0
                                else:
                                    sanitized[i_key] = v
                            DB[k] = sanitized
                        else:
                            DB[k] = {int(i) if str(i).lstrip('-').isdigit() else i: v for i, v in val.items()}
                    
                if OWNER_ID not in DB["ADMIN_IDS"]: DB["ADMIN_IDS"].append(OWNER_ID)
                
                deduplicate_channels_in_memory()
                logger.info("  Data Loaded from MongoDB!")
                return
        except Exception as e: logger.error(f"  MongoDB Load Error: {e}")
        
    logger.info("  Falling back to Local JSON Data...")
    if not os.path.exists(DATA_FILE): save_data_sync(); return
    try:
        with open(DATA_FILE, "r") as f:
            loaded = json.load(f)
            def _get_val(d, k):
                if k in d: return d[k]
                if k.lower() in d: return d[k.lower()]
                return None
                
            val_admin = _get_val(loaded, "ADMIN_IDS")
            if val_admin is not None: DB["ADMIN_IDS"] = [int(x) for x in val_admin if str(x).isdigit()]
            
            val_blocked = _get_val(loaded, "BLOCKED_USERS")
            if val_blocked is not None: DB["BLOCKED_USERS"] = val_blocked
            
            for k in keys_to_load:
                val = _get_val(loaded, k)
                if val is not None: DB[k] = val
            
            for k in ["CUSTOM_WELCOMES", "FREE_CHANNELS", "PAID_CHANNELS", "SPECIAL_CHANNELS", "ALL_CHATS", "USER_TOPICS", "USER_DATA", "PENDING_REQUESTS"]:
                val = _get_val(loaded, k)
                if val is not None: 
                    if k == "USER_TOPICS":
                        sanitized = {}
                        for i, v in val.items():
                            i_key = int(i) if str(i).lstrip('-').isdigit() else i
                            if isinstance(v, dict):
                                sanitized[i_key] = v.get("topic_id") or v.get("message_thread_id") or 0
                            else:
                                sanitized[i_key] = v
                        DB[k] = sanitized
                    else:
                        DB[k] = {int(i) if str(i).lstrip('-').isdigit() else i: v for i, v in val.items()}
                
            if OWNER_ID not in DB["ADMIN_IDS"]: DB["ADMIN_IDS"].append(OWNER_ID)
            
            deduplicate_channels_in_memory()
            logger.info("  Data Loaded from Local JSON!")
    except Exception as e: logger.error(f"  Local Load Error: {e}")

def deduplicate_channels_in_memory():
    """Removes duplicate keys (int vs str) and cross-type duplication across batch collections."""
    for col in ["FREE_CHANNELS", "PAID_CHANNELS", "SPECIAL_CHANNELS", "ALL_CHATS"]:
        normalized = {}
        for k, v in list(DB.get(col, {}).items()):
            try:
                norm_k = int(str(k).strip())
            except Exception:
                norm_k = k
            normalized[norm_k] = v
        DB[col] = normalized

    # Cross-type deduplication: Special > Paid > Free
    special_ids = set(DB.get("SPECIAL_CHANNELS", {}).keys())
    paid_ids = set(DB.get("PAID_CHANNELS", {}).keys())

    # If in Special, remove from Paid & Free
    for sid in special_ids:
        if sid in DB.get("PAID_CHANNELS", {}):
            del DB["PAID_CHANNELS"][sid]
        if sid in DB.get("FREE_CHANNELS", {}):
            del DB["FREE_CHANNELS"][sid]

    # If in Paid, remove from Free
    for pid in paid_ids:
        if pid in DB.get("FREE_CHANNELS", {}):
            del DB["FREE_CHANNELS"][pid]

    # Normalize BATCH_CATEGORIES keys to strings of integer IDs
    if "BATCH_CATEGORIES" in DB and isinstance(DB["BATCH_CATEGORIES"], dict):
        norm_cats = {}
        for k, v in list(DB["BATCH_CATEGORIES"].items()):
            try:
                norm_cats[str(int(str(k).strip()))] = v
            except Exception:
                norm_cats[str(k)] = v
        DB["BATCH_CATEGORIES"] = norm_cats

    # Rebuild ALL_CHATS
    for cid, name in DB.get("FREE_CHANNELS", {}).items():
        DB.setdefault("ALL_CHATS", {})[cid] = name
    for cid, name in DB.get("PAID_CHANNELS", {}).items():
        DB.setdefault("ALL_CHATS", {})[cid] = name
    for cid, name in DB.get("SPECIAL_CHANNELS", {}).items():
        DB.setdefault("ALL_CHATS", {})[cid] = name

def save_data_sync():
    try:
        to_save = {
            "ADMIN_IDS": DB["ADMIN_IDS"], "BLOCKED_USERS": DB["BLOCKED_USERS"],
            "NEW_USERS_ALLOWED": DB.get("NEW_USERS_ALLOWED", True), "FREE_LOCKED": DB.get("FREE_LOCKED", False),
            "PAID_LOCKED": DB.get("PAID_LOCKED", False), "TEST_BOT_LOCKED": DB.get("TEST_BOT_LOCKED", False),
            "LINK_MAP": DB.get("LINK_MAP", {}), "SCHEDULED_DELETES": DB.get("SCHEDULED_DELETES", []),
            "TEST_BOT_LINK": DB.get("TEST_BOT_LINK", ""), 
            "CATEGORIES": DB.get("CATEGORIES", DEFAULT_CATEGORIES),
            "BATCH_CATEGORIES": {str(k): v for k, v in DB.get("BATCH_CATEGORIES", {}).items()},
            "MAINTENANCE_MODE": DB.get("MAINTENANCE_MODE", False),
            "USERBOT_SESSION": DB.get("USERBOT_SESSION"), 
            "USERBOT_PHONE": DB.get("USERBOT_PHONE"),
            "VIP_MATERIALS_LINK": DB.get("VIP_MATERIALS_LINK"),
            "VIP_STICKER_ID": DB.get("VIP_STICKER_ID"),
            "VIP_STICKER_TYPE": DB.get("VIP_STICKER_TYPE"),
            "BATCH_COINS": {str(k): v for k, v in DB.get("BATCH_COINS", {}).items()},
            "CUSTOM_WELCOMES": {str(k): v for k, v in DB.get("CUSTOM_WELCOMES", {}).items()},
            "FREE_CHANNELS": {str(k): v for k, v in DB.get("FREE_CHANNELS", {}).items()},
            "PAID_CHANNELS": {str(k): v for k, v in DB.get("PAID_CHANNELS", {}).items()},
            
            # --- UPDATE: SAVING SPECIAL CHANNELS ---
            "SPECIAL_CHANNELS": {str(k): v for k, v in DB.get("SPECIAL_CHANNELS", {}).items()},
            
            "ALL_CHATS": {str(k): v for k, v in DB.get("ALL_CHATS", {}).items()},
            "USER_DATA": {str(k): v for k, v in DB.get("USER_DATA", {}).items()},
            "USER_TOPICS": {str(k): v for k, v in DB.get("USER_TOPICS", {}).items()},
            "PENDING_REQUESTS": {str(k): v for k, v in DB.get("PENDING_REQUESTS", {}).items()},
            "BATCH_REQUESTS": DB.get("BATCH_REQUESTS", {}),
            "BATCH_REQUEST_STATUS": DB.get("BATCH_REQUEST_STATUS", {}),
            "VOUCHERS": DB.get("VOUCHERS", {}),
            "BATCH_PRICES": {str(k): v for k, v in DB.get("BATCH_PRICES", {}).items()},
            "BATCH_REQUEST_NOTE": DB.get("BATCH_REQUEST_NOTE", ""),
            "BATCH_UPDATE_CHANNEL_ID": DB.get("BATCH_UPDATE_CHANNEL_ID", -1004304703943),
            "BATCH_UPDATE_CHANNEL_LINK": DB.get("BATCH_UPDATE_CHANNEL_LINK", "https://t.me/c/4304703943"),
            "TESTER_IDS": DB.get("TESTER_IDS", []),
            "EXAM_VACANCIES": DB.get("EXAM_VACANCIES", DEFAULT_VACANCIES),
            "CURRENT_AFFAIRS": DB.get("CURRENT_AFFAIRS", DEFAULT_CURRENT_AFFAIRS),
            "MOCK_TESTS": DB.get("MOCK_TESTS", DEFAULT_MOCK_TESTS),
            "MOCK_TEST_RESULTS": DB.get("MOCK_TEST_RESULTS", {}),
            "AD_CONFIG": DB.get("AD_CONFIG", DEFAULT_AD_CONFIG)
        }
        if MONGO_URL and mongo_collection is not None:
            try: 
                mongo_collection.replace_one({"_id": "main_settings"}, {"_id": "main_settings", "data": to_save}, upsert=True)
            except Exception as e: logger.error(f"  MongoDB Save Error: {e}")
        with open(DATA_FILE, "w") as f: json.dump(to_save, f, indent=4)
    except Exception as e: logger.error(f"  Save Error: {e}")

async def _background_save():
    async with data_lock:
        try:
            logger.info("  Saving Data in Background...")
            await asyncio.to_thread(save_data_sync)
            logger.info("  Background Save Complete!")
        except Exception as e:
            logger.error(f"  Background Save Failed: {e}")

SAVE_DEBOUNCE_SECONDS = 4
_save_flush_task = None
_db_is_dirty = False

async def _debounced_flush():
    global _db_is_dirty
    try:
        while _db_is_dirty:
            _db_is_dirty = False
            await asyncio.sleep(SAVE_DEBOUNCE_SECONDS)
            await _background_save()
    except Exception as e:
        logger.error(f"  Debounced Save Failed: {e}")

async def save_data_async():
    global _save_flush_task, _db_is_dirty
    _db_is_dirty = True
    if _save_flush_task is None or _save_flush_task.done():
        _save_flush_task = asyncio.create_task(_debounced_flush())


# --- MEMBERSHIP CACHE ---
_MEMBERSHIP_CACHE = {} 
MEMBERSHIP_CACHE_TTL = 30  

async def get_membership_cached(client, chat_id, user_id, ttl=MEMBERSHIP_CACHE_TTL):
    key = (int(chat_id), int(user_id))
    now = time.time()
    cached = _MEMBERSHIP_CACHE.get(key)
    if cached and cached[1] > now:
        return cached[0]
    try:
        m = await client.get_chat_member(int(chat_id), int(user_id))
        result = m.status in [
            ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER, ChatMemberStatus.RESTRICTED,
        ]
    except Exception:
        result = False
        
    _MEMBERSHIP_CACHE[key] = (result, now + ttl)
    return result

def invalidate_membership_cache(user_id, chat_id=None):
    user_id = int(user_id)
    if chat_id is not None:
        _MEMBERSHIP_CACHE.pop((int(chat_id), user_id), None)
    else:
        for key in [k for k in _MEMBERSHIP_CACHE if k[1] == user_id]:
            del _MEMBERSHIP_CACHE[key]

# --- CORE HELPERS ---
async def execute_universal_kick(user_id, client, permanent_ban=False):
    from pyrogram.errors import FloodWait, UserNotParticipant
    from pyrogram.enums import ChatMemberStatus
    import asyncio
    
    mod = False
    target_uid = int(user_id)
    user_key = target_uid if target_uid in DB.get("USER_DATA", {}) else (str(target_uid) if str(target_uid) in DB.get("USER_DATA", {}) else None)

    # Semaphore limit taaki API block na ho
    sem = asyncio.Semaphore(4)

    # 💎 VIP FILTER LOGIC:
    if permanent_ban:
        # Agar Admin ne manually ban kiya hai, toh sab jagah se nikalo (Including Paid)
        all_channels = set(
            list(DB.get("FREE_CHANNELS", {}).keys()) +
            list(DB.get("PAID_CHANNELS", {}).keys()) +
            list(DB.get("SPECIAL_CHANNELS", {}).keys())
        )
    else:
        # Agar user ne rule toda hai (Leave/Block), toh Paid channel ko safe rakho
        all_channels = set(
            list(DB.get("FREE_CHANNELS", {}).keys()) +
            list(DB.get("SPECIAL_CHANNELS", {}).keys())
        )

    async def _smart_kick(bid):
        nonlocal mod
        async with sem:
            try:
                target_bid = int(bid)
                
                # Check karo ki kya user sach me is channel me hai?
                try:
                    member = await client.get_chat_member(target_bid, target_uid)
                    if member.status in [ChatMemberStatus.LEFT, ChatMemberStatus.BANNED]:
                        return 
                except UserNotParticipant:
                    return
                except Exception:
                    pass
                
                # User andar hai! Ab usko Kick karo.
                bid_str = str(bid)
                is_demo = user_key and bid_str in DB.get("USER_DATA", {}).get(user_key, {}).get("demos", {})
                
                await client.ban_chat_member(target_bid, target_uid)
                if not permanent_ban:
                    await asyncio.sleep(0.5) 
                    await client.unban_chat_member(target_bid, target_uid)
                    
                if is_demo:
                    del DB["USER_DATA"][user_key]["demos"][bid_str]
                    mod = True
                    
            except FloodWait as e:
                await asyncio.sleep(e.value + 1)
                try:
                    await client.ban_chat_member(target_bid, target_uid)
                    if not permanent_ban:
                        await client.unban_chat_member(target_bid, target_uid)
                except Exception:
                    pass
            except Exception as e:
                logger.error(f"  [SMART KICK FAIL] Batch {target_bid}: {e}")

    # Saare filtered channels ke liye smart kick chalayein
    if all_channels:
        await asyncio.gather(*[_smart_kick(bid) for bid in all_channels])
        
    invalidate_membership_cache(target_uid)
    
    if permanent_ban:
        if target_uid not in DB.get("BLOCKED_USERS", []): 
            DB.setdefault("BLOCKED_USERS", []).append(target_uid)
            mod = True
    else:
        if user_key and DB.get("USER_DATA", {}).get(user_key, {}).get("tnc_accepted", False):
            DB["USER_DATA"][user_key]["tnc_accepted"] = False
            mod = True
            
    if mod: await save_data_async()

def is_owner(uid):
    return bool(uid) and str(uid) == str(OWNER_ID)

def is_admin(uid):
    if str(uid) == str(OWNER_ID):
        return True
    uid_str = str(uid)
    for admin_id in DB.get("ADMIN_IDS", []):
        if admin_id == uid or str(admin_id) == uid_str:
            return True
    return False

def check_spam(uid):
    now = time.time(); last = SPAM_CACHE.get(uid, 0)
    SPAM_CACHE[uid] = now
    return True if now - last < 1.5 else False

async def check_membership(user_id, client):
    if is_admin(user_id): return True
    if not MANDATORY_CHANNEL_ID: return True
    return await get_membership_cached(client, MANDATORY_CHANNEL_ID, user_id)

async def is_already_in_channel(client, chat_id, user_id):
    return await get_membership_cached(client, chat_id, user_id)

async def _delayed_delete(client, chat_id, msg_id, delay):
    await asyncio.sleep(delay)
    try:
        await client.delete_messages(chat_id=int(chat_id), message_ids=int(msg_id))
    except Exception:
        pass

async def schedule_delete(client, message, delay=1200):
    if message: 
        asyncio.create_task(_delayed_delete(client, message.chat.id, message.id, delay))

async def refresh_peer_cache(client, chat_id):
    if not chat_id:
        return False
    try:
        await client.get_chat(int(chat_id))
        logger.info(f"  Peer cache refreshed natively for {chat_id}!")
        return True
    except Exception as e:
        logger.error(f"  Native peer refresh failed for {chat_id}: {e}")
        return False

async def get_or_create_topic(user, client, is_retry=False):
    if not SUPPORT_GROUP_ID:
        return None
            
    if user.id in DB.get("USER_TOPICS", {}):
        return DB["USER_TOPICS"][user.id]
            
    if user.id in TOPIC_CREATION_LOCK:
        for _ in range(10):
            await asyncio.sleep(0.5)
            if user.id in DB.get("USER_TOPICS", {}):
                return DB["USER_TOPICS"][user.id]
        return None
            
    TOPIC_CREATION_LOCK.add(user.id)
    
    try:
        title = f"{(user.first_name or 'User')[:20]} ({user.id})"
        topic_id = None
                
        try:
            topic = await client.create_forum_topic(chat_id=int(SUPPORT_GROUP_ID), title=title)
            topic_id = getattr(topic, "id", None) or getattr(topic, "message_thread_id", None)
        except AttributeError:
            from pyrogram.raw.functions.channels import CreateForumTopic
            peer = await client.resolve_peer(int(SUPPORT_GROUP_ID))
            r = await client.invoke(
                CreateForumTopic(channel=peer, title=title, random_id=client.rnd_id())
            )
            for update in r.updates:
                if hasattr(update, "message") and hasattr(update.message, "id"):
                    topic_id = update.message.id
                    break
                            
        if not topic_id:
            raise Exception("Topic ID could not be resolved from Telegram's response.")
                    
        DB.setdefault("USER_TOPICS", {})[user.id] = topic_id
        await save_data_async()
        
        # --- NEW TICKET PINNED MESSAGE ---
        try:
            user_key = str(user.id) if str(user.id) in DB["USER_DATA"] else (user.id if user.id in DB["USER_DATA"] else str(user.id))
            user_data = DB.get("USER_DATA", {}).get(user_key, {})
            referrer_id = user_data.get("referred_by") or user_data.get("pending_referral")
            
            referrer_text = ""
            if referrer_id:
                try:
                    referrer_user = await client.get_users(int(referrer_id))
                    ref_name = referrer_user.first_name or "Referrer"
                    referrer_text = f"\n🔗 **Referrer:** [{ref_name}](tg://user?id={referrer_id})"
                except:
                    referrer_text = f"\n🔗 **Referrer:** `{referrer_id}`"

            pin_text = f"🚨 **NEW USER TICKET**\n👤 [{user.first_name or 'User'}](tg://user?id={user.id})\n🆔 `{user.id}`{referrer_text}"
            
            sent_msg = await client.send_message(
                chat_id=int(SUPPORT_GROUP_ID),
                text=pin_text,
                message_thread_id=topic_id
            )
            await sent_msg.pin()
        except Exception as e:
            logger.error(f"Failed to pin ticket message: {e}")
            
        group_id_str = str(SUPPORT_GROUP_ID).replace("-100", "")
        # Name ko clickable link banaya (tg://user format use karke)
        safe_name = user.first_name or "User"
        
        text = (
            f"🚨 **NEW USER TICKET**\n"
            f"👤 [{safe_name}](tg://user?id={user.id})\n"
            f"🆔 `{user.id}`\n"
        )
                
        try:
            await client.send_message(
                int(SUPPORT_GROUP_ID), text, message_thread_id=topic_id, parse_mode=ParseMode.MARKDOWN
            )
        except TypeError:
            await client.send_message(
                int(SUPPORT_GROUP_ID), text, reply_to_message_id=topic_id, parse_mode=ParseMode.MARKDOWN
            )
                    
        return topic_id
            
    except (PeerIdInvalid, ChannelInvalid, ChannelPrivate) as e:
        if not is_retry:
            logger.warning(f"Peer cache miss for Support Group ({e}). Refreshing natively via get_chat()...")
            TOPIC_CREATION_LOCK.discard(user.id)
            await refresh_peer_cache(client, SUPPORT_GROUP_ID)
            return await get_or_create_topic(user, client, is_retry=True)
        logger.error(f"Topic Creation Error (peer still invalid after native refresh): {e}")
        raise
    except Exception as e:
        logger.error(f"Topic Creation Error: {e}")
        if user.id in DB.get("USER_TOPICS", {}):
            del DB["USER_TOPICS"][user.id]
        raise e
    finally:
        TOPIC_CREATION_LOCK.discard(user.id)

# --- MODERN UI & INTERACTIVE HELPERS ---
def render_progress_bar(current: float, total: float, length: int = 10) -> str:
    """Returns a sleek unicode progress bar (e.g. ▰▰▰▰▰▱▱▱▱▱ 50%)."""
    if total <= 0:
        return "▱" * length + " 0%"
    ratio = min(max(current / total, 0.0), 1.0)
    filled = int(round(ratio * length))
    empty = length - filled
    percent = int(ratio * 100)
    return f"{'▰' * filled}{'▱' * empty} {percent}%"

async def safe_reaction(client, chat_id, message_id, emoji: str = "🔥"):
    """Safely sends an emoji reaction to a message without crashing if disabled."""
    try:
        await client.send_reaction(chat_id=chat_id, message_id=message_id, emoji=emoji)
    except Exception:
        pass

def make_copy_button(text: str, copy_value: str):
    """Creates a Telegram 1-tap CopyTextButton if supported by kurigram, else fallback."""
    try:
        from pyrogram.types import CopyTextButton, InlineKeyboardButton
        return InlineKeyboardButton(text, copy_text=CopyTextButton(text=copy_value))
    except Exception:
        from pyrogram.types import InlineKeyboardButton
        return InlineKeyboardButton(text, callback_data="u_main")
