"""inject_tasks.py — run from ~/projects/Orchestrator/ to inject reviewed tasks."""
import sqlite3, json, hashlib
from datetime import datetime

conn = sqlite3.connect('orchestrator.db')
now = datetime.utcnow().isoformat()

def add(t):
    dh = hashlib.sha256(f"{t['project']}:{t['description']}".encode()).hexdigest()[:16]
    if conn.execute('SELECT 1 FROM tasks WHERE description_hash=?', (dh,)).fetchone():
        print(f'  skip (dup): {t["id"]}'); return
    conn.execute(
        '''INSERT INTO tasks (id,project,description,rationale,effort_category,
           complexity,priority,review_priority,perspective,approval_required,
           depends_on,blocks,status,created_at,description_hash,estimated_tokens)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (t['id'], t['project'], t['description'], t.get('rationale',''),
         t.get('effort_category','feature'), t.get('complexity','medium'),
         t.get('priority',1), t.get('review_priority',3),
         t.get('perspective','engineering_architect'),
         1 if t.get('approval_required') else 0,
         json.dumps(t.get('depends_on',[])), json.dumps(t.get('blocks',[])),
         'queued', now, dh, t.get('estimated_tokens', 8000)))
    print(f'  added {t["id"]}')

tasks = [
    # ── LANG ──────────────────────────────────────────────────────────────────
    {
        'id': 'lang_008',
        'project': 'lang',
        'description': (
            'Write Node.js smoke tests for lang_002 fluency mode: verify fluency_score is '
            'initialised in persistence.js, increments correctly when scaffold not used, '
            'stays 0 when scaffold is used. Runnable with node --test or npm test.'
        ),
        'rationale': 'lang_002 committed with no tests. Need regression coverage before building on top of it.',
        'effort_category': 'test',
        'complexity': 'low',
        'priority': 1,
        'review_priority': 3,
        'perspective': 'qa_tester',
        'estimated_tokens': 6000,
    },
    {
        'id': 'lang_009',
        'project': 'lang',
        'description': (
            'Write Node.js smoke tests for lang_004 scenes: for each of metro_station.js, '
            'market_stall.js, taxi_uber.js, hotel_checkin.js verify the file exports a valid '
            'scene object with id, title, vocab (array >=5 items), phrases (array >=5 items), '
            'beats (array >=3 items). Run with node --test.'
        ),
        'rationale': 'lang_004 generated 4 scenes but no validation that they conform to the expected schema.',
        'effort_category': 'test',
        'complexity': 'low',
        'priority': 1,
        'review_priority': 3,
        'perspective': 'qa_tester',
        'estimated_tokens': 6000,
    },

    # ── MERIDIAN ───────────────────────────────────────────────────────────────
    {
        'id': 'meridian_001',
        'project': 'meridian',
        'description': (
            'Add skeleton loading states to app/(protected)/feed/page.tsx: replace blank '
            'loading state with PostCardSkeleton component (3 stacked cards, animated pulse, '
            'matches PostCard dimensions). Create components/post-card-skeleton.tsx using '
            'Tailwind animate-pulse. Import in feed/loading.tsx.'
        ),
        'rationale': 'Feed shows blank flash before content loads. First impression for publication demo.',
        'effort_category': 'feature',
        'complexity': 'low',
        'priority': 0,
        'review_priority': 4,
        'perspective': 'mobile_ux_designer',
        'estimated_tokens': 8000,
    },
    {
        'id': 'meridian_002',
        'project': 'meridian',
        'description': (
            'Polish app/page.tsx landing page: ensure hero section has a compelling headline '
            'suited to a men\'s fashion publication demo (not placeholder text), editorial card '
            'grid shows 3 structured cards with category labels, sign-in CTA is prominent. '
            'Read the current page.tsx fully before modifying.'
        ),
        'rationale': 'Landing is the first demo screen. Must read as a credible fashion publication.',
        'effort_category': 'feature',
        'complexity': 'medium',
        'priority': 0,
        'review_priority': 4,
        'perspective': 'product_manager',
        'estimated_tokens': 10000,
    },
    {
        'id': 'meridian_003',
        'project': 'meridian',
        'description': (
            'Add mobile bottom navigation bar to app/(protected)/layout.tsx: fixed bottom bar '
            'visible on screens < 768px with 4 Lucide icons (Home/feed, Discover, New Post, '
            'Profile). Hide top nav items on mobile. Add pb-16 to main on mobile so content '
            'is not obscured.'
        ),
        'rationale': 'LAUNCH_CHECKLIST: mobile nav overhaul is a 30-day item. Demo will likely be shown on a phone.',
        'effort_category': 'feature',
        'complexity': 'medium',
        'priority': 0,
        'review_priority': 4,
        'perspective': 'mobile_ux_designer',
        'estimated_tokens': 10000,
    },
    {
        'id': 'meridian_004',
        'project': 'meridian',
        'description': (
            'Add dynamic OG image to app/(protected)/events/[id]: create '
            'app/(protected)/events/[id]/opengraph-image.tsx using Next.js ImageResponse '
            'showing event title, date, and location on a dark background. Read the existing '
            'post OG image implementation for pattern consistency before writing.'
        ),
        'rationale': 'Events are a key demo feature for a fashion publication. OG cards make sharing look credible.',
        'effort_category': 'feature',
        'complexity': 'medium',
        'priority': 1,
        'review_priority': 3,
        'perspective': 'product_manager',
        'estimated_tokens': 8000,
    },
    {
        'id': 'meridian_005',
        'project': 'meridian',
        'description': (
            'Add empty states to app/(protected)/feed/page.tsx and '
            'app/(protected)/discover/[category]/page.tsx: when no posts exist show an inline '
            'SVG placeholder, a headline, and a CTA Button to create a post. '
            'Use existing Button component from components/ui/.'
        ),
        'rationale': 'Demo may have sparse content. Empty white screens look broken; empty states look intentional.',
        'effort_category': 'feature',
        'complexity': 'low',
        'priority': 1,
        'review_priority': 3,
        'perspective': 'mobile_ux_designer',
        'estimated_tokens': 8000,
    },
    {
        'id': 'meridian_006',
        'project': 'meridian',
        'description': (
            'Add editorial typography to globals.css and layout.tsx: load Playfair Display via '
            'next/font/google as a CSS variable, apply to h1/h2 only on landing page hero and '
            'post titles. Keep sans-serif for nav and UI chrome. Add font variable to '
            'tailwind.config.ts.'
        ),
        'rationale': 'Fashion publications use serif display fonts. One font change elevates from web app to publication.',
        'effort_category': 'feature',
        'complexity': 'low',
        'priority': 1,
        'review_priority': 3,
        'perspective': 'mobile_ux_designer',
        'estimated_tokens': 8000,
    },
    {
        'id': 'meridian_007',
        'project': 'meridian',
        'description': (
            'Add error boundaries to feed and post detail: create '
            'app/(protected)/feed/error.tsx and app/post/[id]/error.tsx with icon, friendly '
            'message, and retry button using router.refresh(). Follow Next.js App Router '
            'error.tsx convention.'
        ),
        'rationale': 'Any fetch error currently shows a Next.js crash screen. Error boundaries show a polished fallback.',
        'effort_category': 'feature',
        'complexity': 'low',
        'priority': 2,
        'review_priority': 2,
        'perspective': 'engineering_architect',
        'estimated_tokens': 8000,
    },
]

for t in tasks:
    add(t)

conn.commit()

print()
print('=== Lang queue ===')
for r in conn.execute(
    "SELECT id, status, priority, description FROM tasks WHERE project='lang' ORDER BY priority, id"
).fetchall():
    print(f'  {r[0]:12} [{r[1]:15}] p{r[2]} {r[3][:65]}')

print()
print('=== Meridian queue ===')
for r in conn.execute(
    "SELECT id, status, priority, description FROM tasks WHERE project='meridian' ORDER BY priority, id"
).fetchall():
    print(f'  {r[0]:14} [{r[1]:8}] p{r[2]} {r[3][:65]}')

conn.close()
