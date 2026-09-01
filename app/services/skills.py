"""Skill taxonomy + extraction.

A curated alias map keeps extraction precise (no "Java" inside "JavaScript",
no random capitalised nouns treated as skills). Add your own niche skills to
EXTRA_SKILLS at the bottom -- the rest of the pipeline picks them up
automatically.
"""
from __future__ import annotations

import re

from .textutil import ngrams, tokenize

# canonical name -> aliases (all matched case-insensitively as whole phrases)
SKILL_ALIASES: dict[str, list[str]] = {
    # --- languages ---
    "Python": ["python", "python3", "py"],
    "JavaScript": ["javascript", "js", "es6", "ecmascript"],
    "TypeScript": ["typescript", "ts"],
    "Java": ["java", "java8", "java 11", "java 17"],
    "C#": ["c#", "csharp", "c sharp", ".net c#"],
    "C++": ["c++", "cpp"],
    "C": ["c language", "ansi c", "c programming", "embedded c"],
    "Go": ["golang", "go lang", "go developer", "go programming"],
    "Rust": ["rust", "rustlang"],
    "PHP": ["php", "php8"],
    "Ruby": ["ruby"],
    "Swift": ["swift", "swiftui"],
    "Kotlin": ["kotlin"],
    "Scala": ["scala"],
    "R": ["r language", "r programming", "r studio", "rstudio", "r shiny"],
    "SQL": ["sql", "t-sql", "tsql", "pl/sql", "plsql", "ansi sql"],
    "Bash": ["bash", "shell scripting", "shell script", "zsh"],
    "PowerShell": ["powershell"],
    "Dart": ["dart"],
    "Elixir": ["elixir"],
    "Perl": ["perl"],
    "MATLAB": ["matlab"],
    "Objective-C": ["objective-c", "objective c"],
    "Solidity": ["solidity"],

    # --- frontend ---
    "React": ["react", "react.js", "reactjs"],
    "Next.js": ["next.js", "nextjs"],
    "Vue.js": ["vue", "vue.js", "vuejs", "nuxt", "nuxt.js"],
    "Angular": ["angular", "angularjs", "angular 2"],
    "Svelte": ["svelte", "sveltekit"],
    "Redux": ["redux", "redux toolkit", "rtk query"],
    "HTML": ["html", "html5"],
    "CSS": ["css", "css3", "scss", "sass", "less"],
    "Tailwind CSS": ["tailwind", "tailwind css", "tailwindcss"],
    "Bootstrap": ["bootstrap"],
    "Webpack": ["webpack"],
    "Vite": ["vite"],
    "jQuery": ["jquery"],
    "Web Accessibility": ["accessibility", "wcag", "a11y", "aria"],
    "Responsive Design": ["responsive design", "responsive web design", "mobile first",
                          "mobile first design", "responsive layouts", "cross browser"],
    "Storybook": ["storybook"],
    "Three.js": ["three.js", "threejs", "webgl"],

    # --- backend / API ---
    "Node.js": ["node", "node.js", "nodejs"],
    "Express.js": ["express", "express.js", "expressjs"],
    "NestJS": ["nestjs", "nest.js"],
    "Django": ["django", "django rest framework", "drf"],
    "Flask": ["flask"],
    "FastAPI": ["fastapi"],
    "Spring Boot": ["spring", "spring boot", "springboot"],
    "Laravel": ["laravel"],
    "Ruby on Rails": ["rails", "ruby on rails"],
    ".NET": [".net", "dotnet", "asp.net", "asp.net core", ".net core"],
    "REST APIs": ["rest", "rest api", "restful", "restful api", "rest apis"],
    "GraphQL": ["graphql", "apollo"],
    "gRPC": ["grpc", "protobuf", "protocol buffers"],
    "WebSockets": ["websocket", "websockets", "socket.io"],
    "Microservices": ["microservice", "microservices", "micro-services"],
    "Event-Driven Architecture": ["event driven", "event-driven", "event sourcing", "cqrs"],

    # --- mobile ---
    "React Native": ["react native", "react-native"],
    "Flutter": ["flutter"],
    "Android": ["android", "android sdk", "jetpack compose"],
    "iOS": ["ios", "ios sdk", "xcode", "uikit"],

    # --- data / ML ---
    "Machine Learning": ["machine learning", "ml", "supervised learning"],
    "Deep Learning": ["deep learning", "neural network", "neural networks"],
    "PyTorch": ["pytorch", "torch"],
    "TensorFlow": ["tensorflow", "keras"],
    "scikit-learn": ["scikit-learn", "sklearn", "scikit learn"],
    "Pandas": ["pandas"],
    "NumPy": ["numpy"],
    "NLP": ["nlp", "natural language processing", "text mining"],
    "Computer Vision": ["computer vision", "opencv", "image recognition"],
    "LLMs": ["llm", "llms", "large language model", "large language models", "gpt", "openai api",
             "prompt engineering", "rag", "retrieval augmented generation", "langchain",
             "vector database", "embeddings", "anthropic", "claude api"],
    "MLOps": ["mlops", "model deployment", "mlflow", "kubeflow"],
    "Data Engineering": ["data engineering", "data pipeline", "data pipelines", "etl", "elt"],
    "Apache Spark": ["spark", "pyspark", "apache spark"],
    "Airflow": ["airflow", "apache airflow", "dagster", "prefect"],
    "dbt": ["dbt"],
    "Kafka": ["kafka", "apache kafka"],
    "Data Analysis": ["data analysis", "data analytics", "exploratory data analysis"],
    "Data Visualization": ["data visualization", "data visualisation", "matplotlib", "plotly",
                           "seaborn", "d3.js"],
    "Tableau": ["tableau"],
    "Power BI": ["power bi", "powerbi"],
    "Looker": ["looker", "looker studio"],
    "Statistics": ["statistics", "statistical analysis", "a/b testing", "ab testing",
                   "hypothesis testing", "regression analysis"],
    "Snowflake": ["snowflake"],
    "BigQuery": ["bigquery", "big query"],
    "Redshift": ["redshift"],
    "Databricks": ["databricks"],

    # --- databases ---
    "PostgreSQL": ["postgres", "postgresql", "psql"],
    "MySQL": ["mysql", "mariadb"],
    "MongoDB": ["mongo", "mongodb", "mongoose"],
    "Redis": ["redis"],
    "Elasticsearch": ["elasticsearch", "opensearch", "elk"],
    "SQL Server": ["sql server", "mssql", "microsoft sql server"],
    "Oracle DB": ["oracle db", "oracle database"],
    "DynamoDB": ["dynamodb"],
    "SQLite": ["sqlite"],
    "Cassandra": ["cassandra"],
    "Database Design": ["database design", "data modeling", "data modelling", "schema design",
                        "normalization", "query optimization"],

    # --- cloud / devops ---
    "AWS": ["aws", "amazon web services", "ec2", "s3", "lambda", "cloudformation", "eks", "rds"],
    "Azure": ["azure", "microsoft azure", "azure devops"],
    "GCP": ["gcp", "google cloud", "google cloud platform"],
    "Docker": ["docker", "containerization", "containerisation"],
    "Kubernetes": ["kubernetes", "k8s", "helm", "eks", "aks", "gke"],
    "Terraform": ["terraform", "infrastructure as code", "iac", "pulumi"],
    "CI/CD": ["ci/cd", "cicd", "continuous integration", "continuous deployment",
              "github actions", "gitlab ci", "jenkins", "circleci", "travis ci"],
    "Ansible": ["ansible", "puppet", "chef"],
    "Linux": ["linux", "ubuntu", "unix", "debian", "centos", "rhel"],
    "Nginx": ["nginx", "apache http", "load balancing", "reverse proxy"],
    "Monitoring": ["monitoring", "observability", "prometheus", "grafana", "datadog",
                   "new relic", "splunk", "sentry", "opentelemetry"],
    "Serverless": ["serverless", "aws lambda", "cloud functions", "cloudflare workers"],
    "Site Reliability": ["sre", "site reliability", "incident response", "on-call", "slo", "sli"],
    "Message Queues": ["rabbitmq", "sqs", "message queue", "pub/sub", "celery"],

    # --- security ---
    "Cybersecurity": ["cybersecurity", "cyber security", "information security", "infosec"],
    "Penetration Testing": ["penetration testing", "pentest", "pen testing", "ethical hacking"],
    "OAuth": ["oauth", "oauth2", "openid connect", "oidc", "saml", "sso", "jwt"],
    "Encryption": ["encryption", "cryptography", "tls", "ssl", "pki"],
    "Compliance": ["gdpr", "hipaa", "soc 2", "soc2", "iso 27001", "pci dss"],

    # --- engineering practice ---
    "Git": ["git", "github", "gitlab", "bitbucket", "version control"],
    "Testing": ["unit testing", "unit tests", "integration testing", "test automation",
                "jest", "pytest", "junit", "cypress", "playwright", "selenium", "vitest", "tdd"],
    "Agile": ["agile", "scrum", "kanban", "sprint planning", "sprints"],
    "Code Review": ["code review", "peer review", "pull request", "pull requests"],
    "System Design": ["system design", "distributed systems", "scalability", "high availability",
                      "software architecture", "design patterns", "solid principles"],
    "Performance Optimization": ["performance optimization", "performance tuning", "caching",
                                 "profiling", "latency optimization"],
    "Debugging": ["debugging", "troubleshooting", "root cause analysis"],
    "Documentation": ["technical documentation", "technical writing", "api documentation"],

    # --- product / design / business ---
    "Product Management": ["product management", "product manager", "roadmap", "product strategy",
                           "backlog grooming", "user stories"],
    "UI/UX Design": ["ui/ux", "ux design", "ui design", "user experience", "user interface design",
                     "interaction design", "wireframing", "prototyping"],
    "Figma": ["figma", "sketch app", "adobe xd"],
    "User Research": ["user research", "usability testing", "user interviews", "persona"],
    "Design Systems": ["design system", "design systems", "component library"],
    "Project Management": ["project management", "jira", "confluence", "asana", "trello",
                           "stakeholder management"],
    "Business Analysis": ["business analysis", "business analyst", "requirements gathering",
                          "process mapping", "gap analysis"],
    "SEO": ["seo", "search engine optimization", "sem"],
    "Digital Marketing": ["digital marketing", "content marketing", "email marketing",
                          "google ads", "google analytics", "hubspot", "marketing automation"],
    "Salesforce": ["salesforce", "crm"],
    "Customer Success": ["customer success", "account management", "customer support"],
    "Technical Writing": ["technical writing", "copywriting", "content writing"],
    "Excel": ["excel", "advanced excel", "google sheets", "pivot table", "vlookup"],

    # --- soft skills (weighted lower by the matcher) ---
    "Communication": ["communication skills", "written communication", "verbal communication"],
    "Leadership": ["leadership", "team lead", "mentoring", "mentorship", "people management"],
    "Collaboration": ["collaboration", "cross-functional", "cross functional", "teamwork"],
    "Problem Solving": ["problem solving", "problem-solving", "analytical thinking",
                        "critical thinking"],
    "Remote Work": ["remote work", "distributed team", "async communication",
                    "asynchronous communication"],
}

# Skills that describe how you work rather than what you can build. The matcher
# down-weights these so a JD full of buzzwords cannot fake a high score.
SOFT_SKILLS = frozenset({
    "Communication", "Leadership", "Collaboration", "Problem Solving", "Remote Work",
    "Agile", "Code Review", "Documentation", "Project Management",
})

# Add anything the taxonomy is missing for your field here, same format.
EXTRA_SKILLS: dict[str, list[str]] = {}

SKILL_ALIASES.update(EXTRA_SKILLS)

# Canonical names that are also ordinary words or single letters. Matching these
# literally turns any prose into a skill list -- a German job ad full of "der"
# and "r" was scoring as an R developer -- so only their explicit aliases count.
AMBIGUOUS_CANONICALS = frozenset({"R", "C", "Go"})

# alias phrase -> canonical skill
_LOOKUP: dict[str, str] = {}
_MAX_NGRAM = 1
for _canonical, _aliases in SKILL_ALIASES.items():
    _forms = {a.lower() for a in _aliases}
    if _canonical not in AMBIGUOUS_CANONICALS:
        _forms.add(_canonical.lower())
    # Resumes hyphenate inconsistently -- "Mobile-First Design" vs "mobile first
    # design", "front-end" vs "front end". Register both spellings of every
    # alias so a hyphen does not turn a skill you have into a reported gap.
    for _variant in list(_forms):
        if "-" in _variant:
            _forms.add(_variant.replace("-", " "))
        if " " in _variant:
            _forms.add(_variant.replace(" ", "-"))

    for _alias in _forms:
        if len(_alias) < 2:
            continue  # a bare single letter matches far too much prose
        _LOOKUP[_alias] = _canonical
        _MAX_NGRAM = max(_MAX_NGRAM, len(_alias.split()))


SENIORITY_LEVELS: list[tuple[str, int, list[str]]] = [
    ("intern", 0, ["intern", "internship", "trainee", "apprentice"]),
    ("junior", 1, ["junior", "entry level", "entry-level", "graduate", "associate", "jr."]),
    ("mid", 2, ["mid level", "mid-level", "intermediate"]),
    ("senior", 3, ["senior", "sr.", "sr ", "experienced"]),
    ("lead", 4, ["lead", "principal", "staff engineer", "staff software", "architect", "manager",
                 "head of", "director", "vp ", "chief"]),
]


def extract_skills(text: str) -> list[str]:
    """Return canonical skills mentioned in the text, in order of first appearance."""
    if not text:
        return []

    lowered = " " + re.sub(r"\s+", " ", text.lower()) + " "
    tokens = tokenize(lowered, keep_stopwords=True)

    found: dict[str, int] = {}
    for n in range(_MAX_NGRAM, 0, -1):
        for idx, gram in enumerate(ngrams(tokens, n) if n > 1 else tokens):
            canonical = _LOOKUP.get(gram)
            if canonical and canonical not in found:
                found[canonical] = idx

    # multi-word aliases containing punctuation (c++, ci/cd, .net) survive
    # tokenisation poorly, so also do a direct substring pass for those.
    for alias, canonical in _LOOKUP.items():
        if canonical in found:
            continue
        if not re.search(r"[^a-z0-9 ]", alias):
            continue
        if re.search(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])", lowered):
            found[canonical] = lowered.find(alias)

    return [skill for skill, _ in sorted(found.items(), key=lambda kv: kv[1])]


def skill_weight(skill: str) -> float:
    """Hard skills count more than ways-of-working skills."""
    return 0.4 if skill in SOFT_SKILLS else 1.0


# --------------------------------------------------------------------------- #
# job families
# --------------------------------------------------------------------------- #
# Which profession a title belongs to. Needed because big employers paste the
# same boilerplate into every posting, so a Legal Counsel ad at an engineering
# company still "matches" Git, CSS and Collaboration. Checked in order; the
# first family whose keywords appear wins, so put narrow ones first.
JOB_FAMILIES: list[tuple[str, list[str]]] = [
    ("legal", ["legal counsel", "counsel", "attorney", "paralegal", "lawyer", "litigation",
               "general counsel", "compliance officer"]),
    ("finance", ["accountant", "accounting", "fp&a", "financial analyst", "controller",
                 "treasury", "auditor", "audit", "tax ", "bookkeep", "payroll",
                 "revenue accounting", "corporate finance", "investor relations"]),
    ("people", ["recruiter", "recruiting", "talent acquisition", "people operations",
                "human resources", "hr ", "benefits analyst", "compensation analyst",
                "people partner", "hrbp", "learning and development"]),
    ("sales", ["account executive", "sales", "business development", "partnerships",
               "account manager", "solutions consultant", "pre-sales", "sdr", "bdr",
               "revenue operations", "customer success"]),
    ("marketing", ["marketing", "seo specialist", "content strategist", "brand manager",
                   "communications", "public relations", "demand generation", "copywriter",
                   "social media"]),
    ("support", ["customer support", "technical support", "support engineer", "help desk",
                 "service desk", "customer service"]),
    ("operations", ["logistics", "supply chain", "facilities", "office manager",
                    "procurement", "warehouse", "executive assistant", "administrative"]),
    ("design", ["designer", "ux ", "ui/ux", "user experience", "user interface",
                "creative director", "art director", "illustrator", "design lead"]),
    ("data", ["data scientist", "data analyst", "data engineer", "machine learning",
              "ml engineer", "analytics engineer", "business intelligence", "bi developer",
              "ai engineer", "research scientist"]),
    ("product", ["product manager", "product owner", "program manager", "scrum master",
                 "project manager", "product lead", "technical program"]),
    ("engineering", ["engineer", "developer", "programmer", "architect", "sre",
                     "devops", "qa ", "tester", "technical lead", "cto", "software",
                     "frontend", "front-end", "backend", "back-end", "fullstack",
                     "full-stack", "android", "ios ", "mobile", "platform", "security",
                     "infrastructure", "webmaster"]),
]

# Families close enough that moving between them is a normal career step, so a
# mismatch inside this set is a nudge rather than a disqualification.
TECH_CLUSTER = frozenset({"engineering", "data", "design", "product"})


def job_family(title: str) -> str:
    """Best-guess profession for a job title. '' when nothing matches."""
    lowered = f" {(title or '').lower()} "
    for family, keywords in JOB_FAMILIES:
        if any(keyword in lowered for keyword in keywords):
            return family
    return ""


def families_for(titles: list[str]) -> set[str]:
    return {f for f in (job_family(t) for t in titles if t) if f}


def detect_seniority(text: str) -> tuple[str, int]:
    """Return (label, rank). Rank 2 ('mid') is the neutral default."""
    lowered = " " + (text or "").lower() + " "
    best: tuple[str, int] | None = None
    for label, rank, keywords in SENIORITY_LEVELS:
        for kw in keywords:
            if kw in lowered:
                if best is None or rank > best[1]:
                    best = (label, rank)
                break
    return best or ("mid", 2)


def years_required(text: str) -> float | None:
    """Pull the smallest 'N+ years' requirement out of a job description."""
    if not text:
        return None
    matches = re.findall(
        r"(\d{1,2})\s*(?:\+|plus)?\s*(?:-\s*\d{1,2}\s*)?year", (text or "").lower()
    )
    values = [float(m) for m in matches if 0 < float(m) <= 25]
    return min(values) if values else None
