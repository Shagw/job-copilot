# User guide

How to use Job Application Copilot to go from a job posting to a tailored resume and cover letter in a few
minutes. The AI suggests; you review and decide. Nothing is ever sent to an employer for you.

## 1. Create your account

1. Open the app and click **Create an account**.
2. Enter your email and a password.
3. Check your email for a **6-digit code** and enter it on the **Verify your email** page.

Forgot your password? Click **Forgot password?** on the login page, and we'll email you a code to set a new one.

## 2. Upload your master resume (once)

Go to **Resume** in the menu and upload your resume as **PDF, DOCX or TXT**. This is your "master" resume:
everything the AI writes about you comes from it (and from notes you add later). It's never changed; each job gets
its own tailored copy.

Tips:
- Use a text-based PDF (one you can select text in), not a scanned image.
- Put every skill, tool and project you've actually used in it, even briefly. The AI won't claim anything it
  can't find.
- Upload a new version any time; new jobs use the latest one.

## 3. Add a job

Click **New job** and either:
- **Paste description**: paste the full posting (best results), or
- **Use a link**: a public career page (Greenhouse, Lever, company sites). LinkedIn and Indeed block this, so
  paste those.

Click **Analyse job**.

## 4. Step 1 – Job: check what the AI understood

You'll see the job title, company, **Must-have requirements**, **Nice to have** and **ATS keywords** (the words
applicant tracking systems scan for). Fix anything that's wrong, since everything after this uses it, then click
**Approve & score my fit**.

## 5. Step 2 – Fit: how well you match

- A **score from 0 to 100** and a verdict (strong / moderate / weak fit).
- Every requirement marked **strong**, **partial** or **missing**, with the **exact line from your resume** that
  proves it.
- Gaps and advice.

"N+ years of X" requirements are checked against the dates of the roles where you used X, so the score is
honest, not flattering.

**Notes for the resume tailor (optional)** are your chance to add what the resume doesn't say:
- "I've used MongoDB in side projects, add it."
- "Emphasise leadership."
- "Keep it to one page."

Skills you state in notes are treated as **true** and may be added to your resume, so only write what you'd
defend in an interview. Then click **Tailor my resume**.

## 6. Step 3 – Resume: review, improve, repeat

You get a tailored copy of your resume shown as a **PDF preview**. On a wide screen the checks sit in a panel on
the right that stays in view while you scroll:

| Report item | Meaning |
|-------------|---------|
| **Covered** | Job keywords your tailored resume now shows. |
| **In your resume, but missing from the tailored version** | Keywords you have that got left out. Normally empty; if not, ask for them in a re-tailor. |
| **Not added (no evidence in your resume)** | Keywords the job wants that neither your resume nor your notes support. They're left out on purpose. Add a note if you really have them. |
| **Check these lines before using the resume** | Lines the checks couldn't match to your resume (a new number or skill). Fix or remove them. |
| **Keywords added** | Job keywords the tailored version now shows, and the section they went into. |
| **These results from your resume are missing** | Numbers from your resume (40%, 1M+ users, $2M) that the tailored version lost. Put them back or ask for it in a re-tailor. |
| **Recruiter check** | What a recruiter notices in 15 seconds: top skills near the top, measurable impact, length, no keyword stuffing, and (when enabled) whether you look relevant fast, the summary is specific, the current role is clear, bullets are concise and formatting is clean. Each ⚠️ says what to fix. |

**Edit it yourself.** Click **Edit text**, change anything, then **Save & preview**. Keep headings in CAPITALS and
start bullets with "- " for the best PDF layout.

**Or ask for changes, as many times as you like.** Under **Ask for changes**, type what you want and click
**Re-tailor with these changes**. For example:
- "Fix the formatting."
- "Improve the summary so it leads with backend work."
- "Rewrite the projects section, one line per project."
- "Add a certifications section: AWS Cloud Practitioner (2025)."
- "Add Docker and Redis to skills, I use both at work."

Each round works on the **current** text (including your unsaved edits), changes only what you asked, and
re-runs all the checks. Things you told it in earlier rounds stay true. The heading shows how many times you've
revised it.

When you're happy: **Download PDF** / **Download DOCX**, or add **Notes for the cover letter (optional)** (why
this company, tone, anything personal) and click **Approve & write cover letter**.

## 7. Step 4 – Cover letter

A Writer drafts the letter and a Critic reviews it (up to 3 rounds). You see the Critic's score and what it
flagged. Letters with invented numbers, placeholders like "[Company]", or the wrong length are never approved.

Edit the letter directly, set the **Application status** (draft, applied, interview, rejected, offer) and click
**Save**. Download the letter and the resume as PDF or DOCX.

## 8. History

Your recent jobs are listed on the home page with their status, so you can come back and continue any step. Jobs
are shown for **3 days**, then archived. They're never deleted, just hidden from the list.

## "How the agent got here"

Under each step you can open the reasoning trace: what was searched, which model or check ran, and how many
tokens it used. It's there so you can see *why* the AI said something, not just what it said.

## Good to know

- **Your data is private to your account.** No other user can see your resume or jobs. To do its work, your
  resume and job text are sent to the AI providers the site uses (Groq, and TypeSafe if enabled).
- **"AI is busy, try again in N seconds"**: the free AI capacity is shared and briefly used up. Wait and click
  again; nothing is lost.
- **The AI never applies for you** and never logs into job sites. You copy, download and submit yourself.
- **Always read the final documents.** The checks catch invented numbers and skills, but you're the one sending
  them to an employer.

Running your own copy instead? See the [fork guide](FORKING.md).
