import re
import time
import logging
import httpx
from config import GROQ_API_KEYS

logger = logging.getLogger(__name__)

_groq_key_idx = 0
_groq_last_calls: dict[int, float] = {}

ROLE_KEYWORDS: dict[str, set[str]] = {
    "ai_ml_engineer": {
        "machine learning", "ml engineer", "ml developer", "ai engineer",
        "deep learning", "nlp", "natural language processing", "computer vision",
        "llm", "artificial intelligence", "pytorch", "tensorflow", "keras",
        "mlops", "ml ops", "ai/ml", "ml/ai", "ai developer", "ai researcher",
        "machine learning engineer", "applied scientist", "research scientist",
        "generative ai", "gen ai", "genai", "large language model",
        "neural network", "reinforcement learning", "hugging face", "huggingface",
        "transformers", "stable diffusion", "prompt engineer", "ai platform",
        "ml platform", "data science", "scikit-learn", "sklearn",
        "model training", "model deployment", "feature engineering",
        "recommendation system", "cv engineer", "speech recognition",
        "image recognition", "object detection",
    },
    "data_engineer": {
        "data engineer", "data engineering", "data pipeline", "etl developer",
        "etl engineer", "data infrastructure", "data platform",
        "big data engineer", "big data", "data warehouse", "dwh",
        "apache spark", "spark developer", "airflow", "kafka",
        "databricks", "snowflake engineer", "redshift", "bigquery",
        "data lake", "data lakehouse", "data integration",
        "data architect", "dbt", "hadoop", "hive", "presto", "trino",
        "flink", "beam", "nifi", "data ops", "dataops",
        "streaming data", "batch processing", "data modeling",
        "dimensional modeling", "data mesh",
    },
    "data_scientist": {
        "data scientist", "data science", "applied scientist",
        "research scientist", "quantitative analyst", "quant",
        "statistical modeling", "predictive modeling", "analytics engineer",
        "experimentation", "a/b testing", "ab testing", "hypothesis testing",
        "bayesian", "statistical analysis", "r programmer",
        "jupyter", "pandas", "numpy", "scipy", "matplotlib",
        "causal inference", "time series", "forecasting",
        "regression", "classification", "clustering",
    },
    "data_analyst_bi": {
        "data analyst", "data analysis", "bi analyst", "bi developer",
        "bi engineer", "business intelligence", "reporting analyst",
        "analytics analyst", "sql analyst", "tableau developer",
        "power bi", "powerbi", "looker", "metabase", "superset",
        "dashboard", "data visualization", "data viz",
        "excel analyst", "google analytics", "mixpanel",
        "amplitude", "reporting engineer", "insight analyst",
        "operations analyst", "marketing analyst",
    },
    "business_analyst": {
        "business analyst", "business analysis", "ba ",
        "requirements analyst", "process analyst", "systems analyst",
        "functional analyst", "business systems analyst",
        "product analyst", "strategy analyst", "management consultant",
        "business process", "bpm", "business requirements",
        "stakeholder management", "gap analysis", "use case",
        "user story", "jira", "confluence", "agile analyst",
        "scrum master",
    },
    "full_stack_developer": {
        "full stack", "full-stack", "fullstack", "mern", "mean stack",
        "mern stack", "pern stack", "lamp stack",
        "full stack developer", "full-stack developer",
        "full stack engineer", "full-stack engineer",
        "fullstack developer", "fullstack engineer",
        "t-shaped developer", "generalist developer",
        "web application developer",
    },
    "backend_engineer": {
        "backend", "back-end", "back end", "api developer",
        "python developer", "node.js developer", "nodejs developer",
        "golang developer", "go developer", "java developer",
        "ruby developer", "rails developer", "django developer",
        "flask developer", "fastapi", "spring boot", "spring developer",
        "server-side", "server side", "microservices",
        "backend engineer", "back-end engineer", "api engineer",
        "rust developer", "c# developer", "dotnet developer",
        ".net developer", "php developer", "laravel developer",
        "express.js", "nestjs", "graphql developer",
        "grpc", "rest api", "restful", "elixir developer",
        "scala developer", "kotlin developer",
    },
    "frontend_engineer": {
        "frontend", "front-end", "front end", "react developer",
        "vue developer", "angular developer", "ui developer",
        "javascript developer", "typescript developer", "react engineer",
        "frontend engineer", "front-end engineer", "vue engineer",
        "angular engineer", "svelte", "next.js", "nextjs",
        "nuxt", "gatsby", "webpack", "vite developer",
        "css developer", "html developer", "web ui",
        "react native developer", "ember", "tailwind",
        "sass developer", "responsive design",
    },
    "software_engineer": {
        "software engineer", "software developer", "sde",
        "software development engineer", "programmer", "coder",
        "application developer", "application engineer",
        "platform engineer", "systems engineer", "solutions engineer",
        "integration engineer", "r&d engineer", "tools engineer",
        "build engineer", "release engineer", "site reliability",
        "sre", "infrastructure engineer", "distributed systems",
        "embedded software", "firmware engineer",
        "software architect", "technical lead",
    },
    "web_developer": {
        "web developer", "web designer", "web engineer",
        "wordpress developer", "shopify developer", "webflow",
        "squarespace", "wix developer", "drupal developer",
        "joomla", "web master", "webmaster",
        "website developer", "website designer",
        "html css", "web application",
        "jamstack", "static site", "cms developer",
        "ecommerce developer", "e-commerce developer",
        "magento", "woocommerce", "prestashop",
    },
    "mobile_app_developer": {
        "mobile developer", "mobile engineer", "mobile app",
        "ios developer", "ios engineer", "android developer",
        "android engineer", "swift developer", "kotlin developer",
        "react native", "flutter developer", "flutter engineer",
        "dart developer", "xamarin", "ionic developer",
        "mobile application", "app developer", "app engineer",
        "swiftui", "jetpack compose", "objective-c",
        "mobile platform", "cross-platform", "hybrid app",
    },
    "cloud_devops_engineer": {
        "devops", "dev ops", "cloud engineer", "cloud architect",
        "aws engineer", "azure engineer", "gcp engineer",
        "cloud developer", "cloud consultant", "cloud specialist",
        "kubernetes", "k8s", "docker", "terraform", "ansible",
        "jenkins", "ci/cd", "ci cd", "cicd",
        "infrastructure as code", "iac", "cloudformation",
        "helm", "argocd", "argo cd", "gitops",
        "linux administrator", "linux engineer", "sysadmin",
        "system administrator", "network engineer",
        "devsecops", "platform engineer", "reliability engineer",
        "puppet", "chef", "saltstack", "prometheus", "grafana",
        "monitoring engineer", "observability",
        "aws", "azure", "gcp", "google cloud",
    },
    "cybersecurity_analyst": {
        "cybersecurity", "cyber security", "security analyst",
        "security engineer", "infosec", "information security",
        "penetration tester", "pen tester", "pentest",
        "ethical hacker", "security consultant",
        "soc analyst", "threat analyst", "vulnerability",
        "incident response", "forensics", "malware analyst",
        "security operations", "compliance analyst",
        "identity access", "iam", "siem", "soar",
        "network security", "application security", "appsec",
        "cloud security", "grc", "risk analyst",
        "security architect", "ciso", "devsecops",
    },
    "software_tester_qa": {
        "qa engineer", "qa analyst", "quality assurance",
        "software tester", "test engineer", "sdet",
        "automation tester", "test automation", "manual tester",
        "manual testing", "selenium", "cypress", "playwright",
        "appium", "test lead", "quality engineer",
        "performance tester", "load testing", "jmeter",
        "regression testing", "functional testing",
        "qa developer", "testing engineer", "qa lead",
        "test architect", "api testing", "postman",
    },
    "ui_ux_engineer": {
        "ui/ux", "ux engineer", "ui engineer", "ux developer",
        "ux designer", "ui designer", "ui/ux designer",
        "interaction designer", "user experience",
        "user interface", "usability", "user research",
        "ux researcher", "design system", "figma developer",
        "prototyping", "wireframe", "information architect",
        "accessibility engineer", "a11y",
        "design engineer", "ux/ui",
    },
    "product_designer": {
        "product designer", "product design", "design lead",
        "visual designer", "brand designer", "creative designer",
        "design manager", "design director", "figma",
        "sketch designer", "adobe xd", "invision",
        "motion designer", "illustration", "iconography",
        "design thinking", "design sprint",
        "creative director", "art director",
    },
    "graphic_designer": {
        "graphic designer", "graphic design", "visual design",
        "photoshop", "illustrator", "indesign", "canva",
        "print designer", "branding designer", "logo designer",
        "packaging designer", "layout designer",
        "creative designer", "multimedia designer",
        "digital designer", "print design",
        "adobe creative", "coreldraw",
    },
    "seo_specialist": {
        "seo specialist", "seo analyst", "seo manager",
        "seo consultant", "seo expert", "seo executive",
        "search engine optimization", "sem specialist",
        "search engine marketing", "content strategist",
        "digital marketing", "growth hacker", "growth marketing",
        "keyword research", "link building", "technical seo",
        "on-page seo", "off-page seo", "serp",
        "google search console", "ahrefs", "semrush", "moz",
        "content marketing", "organic traffic",
    },
    "video_editor": {
        "video editor", "video producer", "video specialist",
        "motion graphics", "after effects", "premiere pro",
        "davinci resolve", "final cut", "video production",
        "video content", "cinematographer", "videographer",
        "colorist", "color grading", "vfx artist",
        "visual effects", "compositing", "animation",
        "video post-production", "youtube editor",
        "reels editor", "short form video",
    },
    "account_manager": {
        "account manager", "account executive", "account director",
        "key account", "client manager", "client success",
        "customer success", "client relationship",
        "relationship manager", "partner manager",
        "strategic account", "enterprise account",
        "client engagement", "account lead",
        "customer relationship", "crm manager",
    },
    "sales_representative": {
        "sales representative", "sales rep", "sales executive",
        "sales associate", "sales engineer", "sales manager",
        "sales development", "sdr", "bdr",
        "business development", "inside sales", "outside sales",
        "field sales", "sales consultant", "sales specialist",
        "pre-sales", "presales", "sales lead",
        "revenue", "quota", "pipeline",
        "territory manager", "channel sales",
    },
}

TECH_INDICATORS: set[str] = {
    "software", "developer", "engineer", "programmer", "coder",
    "devops", "frontend", "backend", "full stack", "fullstack",
    "data", "cloud", "aws", "azure", "gcp", "kubernetes", "docker",
    "python", "java", "javascript", "typescript", "react", "angular", "vue",
    "node.js", "nodejs", "golang", "rust", "c++", "c#", "ruby",
    "machine learning", "ai", "ml", "deep learning", "nlp",
    "database", "sql", "nosql", "mongodb", "postgresql", "mysql",
    "api", "microservices", "cybersecurity", "security",
    "qa", "testing", "automation", "ci/cd", "cicd",
    "ui", "ux", "figma", "design system",
    "mobile", "ios", "android", "flutter", "react native",
    "seo", "analytics", "bi ", "tableau", "power bi",
    "web", "html", "css", "sass", "webpack",
    "git", "linux", "terraform", "ansible",
    "graphic design", "photoshop", "illustrator", "after effects",
    "video editor", "motion graphics", "premiere",
    "sre", "reliability", "infrastructure",
    "embedded", "firmware", "iot",
    "blockchain", "web3", "smart contract", "solidity",
    "technical", "tech", "it ", "information technology",
}

SENIORITY_PREFIXES = re.compile(
    r"\b(senior|sr\.?|lead|staff|principal|junior|jr\.?|intern|entry[- ]level|"
    r"mid[- ]level|associate|chief|head of|director of|vp of|manager of)\b",
    re.IGNORECASE,
)


def _normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^\w\s/+#.-]", " ", text)
    text = SENIORITY_PREFIXES.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def is_tech_job(title: str, tags: list[str]) -> bool:
    combined = _normalize(title) + " " + " ".join(t.lower() for t in tags)
    return any(kw in combined for kw in TECH_INDICATORS)


def match_role(job: dict) -> tuple[str, float]:
    title_raw = job.get("title", "")
    tags_raw = job.get("tags", [])
    description_raw = job.get("description", "")

    if not is_tech_job(title_raw, tags_raw):
        return ("", 0.0)

    title = _normalize(title_raw)
    tags_text = " ".join(_normalize(t) for t in tags_raw)
    desc_text = _normalize(description_raw[:500])

    scores: dict[str, int] = {}

    for role_key, keywords in ROLE_KEYWORDS.items():
        score = 0
        for kw in keywords:
            kw_lower = kw.lower()
            if kw_lower in title:
                score += 5
            if kw_lower in tags_text:
                score += 2
            if kw_lower in desc_text:
                score += 1
        scores[role_key] = score

    best_role = max(scores, key=scores.get)
    best_score = scores[best_role]

    if best_score < 3:
        return ("software_engineer", 0.2)

    confidence = min(best_score / 15.0, 1.0)
    return (best_role, round(confidence, 2))


VALID_ROLE_KEYS = set(ROLE_KEYWORDS.keys())


def match_role_with_ai(job: dict) -> tuple[str, float] | None:
    """Use Groq LLM to match ambiguous jobs. Round-robins across all Groq keys."""
    global _groq_key_idx
    if not GROQ_API_KEYS:
        return None

    title = job.get("title", "")
    tags = ", ".join(job.get("tags", [])[:10])
    desc = job.get("description", "")[:300]
    role_list = ", ".join(sorted(VALID_ROLE_KEYS))

    prompt = (
        f"Given this job posting, pick the single best matching role key from this list:\n"
        f"{role_list}\n\n"
        f"Job title: {title}\nTags: {tags}\nDescription: {desc}\n\n"
        f"Reply with ONLY the role_key, nothing else."
    )

    for attempt in range(len(GROQ_API_KEYS)):
        idx = (_groq_key_idx + attempt) % len(GROQ_API_KEYS)
        key = GROQ_API_KEYS[idx]
        last = _groq_last_calls.get(idx, 0.0)
        elapsed = time.time() - last
        if elapsed < 2.5:
            time.sleep(2.5 - elapsed)
        _groq_last_calls[idx] = time.time()
        try:
            resp = httpx.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={
                    "model": "llama-3.1-8b-instant",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 30,
                    "temperature": 0,
                },
                timeout=10,
            )
            if resp.status_code == 200:
                _groq_key_idx = (idx + 1) % len(GROQ_API_KEYS)
                answer = resp.json()["choices"][0]["message"]["content"].strip().lower()
                answer = answer.replace('"', '').replace("'", '').strip()
                if answer in VALID_ROLE_KEYS:
                    return (answer, 0.85)
                return None
            if resp.status_code == 429:
                logger.debug("Groq key #%d rate limited, rotating to next", idx + 1)
                continue
            logger.debug("Groq key #%d returned %d", idx + 1, resp.status_code)
        except Exception as e:
            logger.debug("Groq key #%d match failed: %s", idx + 1, e)
    return None


def smart_match_role(job: dict) -> tuple[str, float]:
    """Keyword match first; if confidence is low, ask Groq for a better match."""
    role_key, confidence = match_role(job)
    if confidence >= 0.5:
        return (role_key, confidence)
    ai_result = match_role_with_ai(job)
    if ai_result and ai_result[1] > confidence:
        return ai_result
    return (role_key, confidence)
