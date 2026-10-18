// Executive deck for the CEO: Getting our filings ready for AI (non-technical)
const pptxgen = require("pptxgenjs");
const React = require("react");
const ReactDOMServer = require("react-dom/server");
const sharp = require("sharp");
const fa = require("react-icons/fa");
const { applyTheme } = require(process.env.SKILL_DIR + "/scripts/apply_theme.js");

const OUT = process.argv[2];
const THEME = {
  name: "CER Filing for AI",
  headFontFace: "Cambria",
  bodyFontFace: "Calibri",
  colors: {
    dk1: "1F2A33", lt1: "FFFFFF", dk2: "0E4D57", lt2: "EEF3F4",
    accent1: "D9731A", accent2: "0E4D57", accent3: "6B7C85", accent4: "2D7F8C",
    accent5: "B8C6CC", accent6: "B23A2E", hlink: "0E4D57", folHlink: "5A6B73",
  },
};
const HEX = THEME.colors;

async function icon(Comp, color, size = 256) {
  const svg = ReactDOMServer.renderToStaticMarkup(React.createElement(Comp, { color: "#" + color, size: String(size) }));
  return "image/png;base64," + (await sharp(Buffer.from(svg)).png().toBuffer()).toString("base64");
}

(async () => {
  const pres = new pptxgen();
  pres.layout = "LAYOUT_WIDE"; // 13.33 x 7.5 in
  pres.title = "Getting our filings ready for AI";
  pres.author = "Jason Vriends";
  pres.subject = "What reading every REGDOCS filing taught us, and what it will take";
  pres.theme = { headFontFace: THEME.headFontFace, bodyFontFace: THEME.bodyFontFace };
  const C = pres.SchemeColor;

  pres.defineSlideMaster({
    title: "Title Dark", background: { color: C.text2 },
    objects: [
      { placeholder: { options: { name: "title", type: "title", x: 0.8, y: 2.0, w: 11.7, h: 1.8, fontSize: 48, bold: true, color: C.background1, valign: "bottom", margin: 0 }, text: "" } },
      { placeholder: { options: { name: "body", type: "body", x: 0.8, y: 4.0, w: 11.0, h: 1.0, fontSize: 22, color: C.background2, valign: "top", margin: 0 }, text: "" } },
    ],
  });
  pres.defineSlideMaster({
    title: "Content", background: { color: C.background1 },
    objects: [
      { placeholder: { options: { name: "title", type: "title", x: 0.6, y: 0.4, w: 12.1, h: 0.9, fontSize: 34, bold: true, color: C.text2, valign: "middle", margin: 0 }, text: "" } },
      { text: { text: "Canada Energy Regulator  ·  October 2026", options: { x: 0.6, y: 7.0, w: 6, h: 0.3, fontSize: 10, color: C.accent3, margin: 0 } } },
    ],
    slideNumber: { x: 12.2, y: 7.0, w: 0.5, h: 0.3, fontSize: 10, color: C.accent3, align: "right" },
  });
  pres.defineSlideMaster({
    title: "Statement Dark", background: { color: C.text2 },
    objects: [
      { placeholder: { options: { name: "title", type: "title", x: 0.6, y: 0.4, w: 12.1, h: 0.9, fontSize: 34, bold: true, color: C.background1, valign: "middle", margin: 0 }, text: "" } },
    ],
    slideNumber: { x: 12.2, y: 7.0, w: 0.5, h: 0.3, fontSize: 10, color: C.background2, align: "right" },
  });

  const T = (s, text, o) => s.addText(text, { margin: 0, isTextBox: true, ...o });
  const big = (s, n, label, x, y, w, color, size = 54, labelColor) => {
    T(s, n, { x, y, w, h: 1.0, fontFace: THEME.headFontFace, fontSize: size, bold: true, color });
    T(s, label, { x, y: y + 1.0, w, h: 0.75, fontSize: 16, color: labelColor || C.text1, valign: "top" });
  };
  const iconRow = async (s, Comp, y, head, body, headColor) => {
    s.addShape(pres.shapes.OVAL, { x: 0.6, y, w: 0.85, h: 0.85, fill: { color: C.text2 }, line: { type: "none" } });
    s.addImage({ data: await icon(Comp, HEX.lt1), x: 0.8, y: y + 0.2, w: 0.45, h: 0.45 });
    T(s, head, { x: 1.75, y: y - 0.05, w: 10.9, h: 0.45, fontSize: 20, bold: true, color: headColor || C.text2 });
    T(s, body, { x: 1.75, y: y + 0.4, w: 10.9, h: 0.55, fontSize: 16, color: C.text1, valign: "top" });
  };
  const card = (s, x, y, w, h, dark) => s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w, h, fill: { color: dark ? C.text2 : C.background2 }, line: { type: "none" }, rectRadius: 0.15 });

  // 1. Title
  pres.addSection({ title: "The record" });
  let s = pres.addSlide({ masterName: "Title Dark", sectionTitle: "The record" });
  s.addText("Getting our filings ready for AI", { placeholder: "title" });
  s.addText("What reading every document in REGDOCS taught us — and what it will take", { placeholder: "body" });
  T(s, "Canada Energy Regulator  ·  October 2026", { x: 0.8, y: 6.5, w: 8, h: 0.4, fontSize: 14, color: C.background2 });
  s.addNotes("We set out to answer a simple question: if the CER wants to use AI to speed up its work - including permitting - can it trust what is in our own public record? To find out, every document in REGDOCS was read, page by page. This is what that taught us, and what it will take to get ready.");

  // 2. How big the record is
  s = pres.addSlide({ masterName: "Content", sectionTitle: "The record" });
  s.addText("Our public record is enormous", { placeholder: "title" });
  big(s, "269,025", "documents filed since 2002", 0.6, 1.7, 3.9, C.text2, 44);
  big(s, "3.6 million", "pages", 4.7, 1.7, 3.9, C.text2, 44);
  big(s, "1.2 billion", "words", 8.8, 1.7, 3.9, C.text2, 44);
  card(s, 0.6, 4.0, 5.9, 2.6, false);
  s.addImage({ data: await icon(fa.FaLayerGroup, HEX.dk2), x: 0.95, y: 4.35, w: 0.7, h: 0.7 });
  T(s, "Printed, a stack about 360 metres tall", { x: 1.9, y: 4.3, w: 4.4, h: 0.85, fontSize: 20, bold: true, color: C.text2, valign: "middle" });
  T(s, "nearly twice the height of the Calgary Tower", { x: 1.9, y: 5.2, w: 4.4, h: 0.9, fontSize: 16, color: C.text1, valign: "top" });
  card(s, 6.8, 4.0, 5.9, 2.6, false);
  s.addImage({ data: await icon(fa.FaBook, HEX.dk2), x: 7.15, y: 4.35, w: 0.7, h: 0.7 });
  T(s, "About 13,000 novels’ worth of text", { x: 8.1, y: 4.3, w: 4.4, h: 0.85, fontSize: 20, bold: true, color: C.text2, valign: "middle" });
  T(s, "applications, evidence, letters, studies, drawings and decisions", { x: 8.1, y: 5.2, w: 4.4, h: 0.9, fontSize: 16, color: C.text1, valign: "top" });
  s.addNotes("This is the size of our public record: over a quarter of a million documents and three and a half million pages since 2002. Printed and stacked, it would stand about 360 metres - nearly twice the Calgary Tower. It is about as much text as thirteen thousand novels: applications, evidence, letters, environmental studies, drawings and our own decisions. (Paper about 0.1 mm a sheet; a novel about 90,000 words.)");

  // 3. By hand vs automation
  s = pres.addSlide({ masterName: "Content", sectionTitle: "The record" });
  s.addText("Reading it all by hand is not realistic", { placeholder: "title" });
  card(s, 0.6, 1.6, 5.9, 4.6, false);
  s.addImage({ data: await icon(fa.FaUserClock, HEX.dk2), x: 0.95, y: 1.95, w: 0.8, h: 0.8 });
  T(s, "By hand", { x: 2.0, y: 2.0, w: 4.2, h: 0.7, fontSize: 24, bold: true, color: C.text2, valign: "middle" });
  big(s, "~75 years", "for one person reading full-time, before checking or fixing anything", 0.95, 3.0, 5.2, C.text2, 54);
  card(s, 6.8, 1.6, 5.9, 4.6, true);
  s.addImage({ data: await icon(fa.FaBolt, HEX.accent1), x: 7.15, y: 1.95, w: 0.8, h: 0.8 });
  T(s, "With automation", { x: 8.2, y: 2.0, w: 4.2, h: 0.7, fontSize: 24, bold: true, color: C.background1, valign: "middle" });
  big(s, "Under a week", "on one computer: every page read, compared and catalogued", 7.15, 3.0, 5.2, C.accent1, 54, C.background2);
  T(s, "Estimate: about two minutes to read a page; 1,600 working hours a year.", { x: 0.6, y: 6.4, w: 12.1, h: 0.35, fontSize: 11, color: C.accent3 });
  s.addNotes("If a person tried to read the whole record - just read it, at about two minutes a page - it would take one person around seventy-five years of full-time work, before checking or correcting anything. With automation, every page was read, compared and catalogued in under a week on a single computer. That is the promise of AI for the CER. The rest of this presentation is about what stands in its way.");

  // 4. Headline finding
  pres.addSection({ title: "The problems" });
  s = pres.addSlide({ masterName: "Statement Dark", sectionTitle: "The problems" });
  s.addText("What we found", { placeholder: "title" });
  T(s, "1 in 4", { x: 0.6, y: 1.9, w: 5.6, h: 2.2, fontFace: THEME.headFontFace, fontSize: 108, bold: true, color: C.accent1 });
  T(s, "documents cannot be read reliably by a computer", { x: 6.4, y: 2.2, w: 6.3, h: 1.6, fontSize: 32, bold: true, color: C.background1, valign: "middle" });
  T(s, "AI can only be as good as what it reads. Today, a quarter of our record would give it pictures to guess at, or text that is wrong.", { x: 0.6, y: 4.7, w: 12.1, h: 1.2, fontSize: 22, color: C.background2, valign: "top" });
  s.addNotes("Here is the headline. About one document in four in our record cannot be read reliably by a computer. Any AI tool we put on top of this record - search, summaries, assistants for reviewers - can only be as good as what it reads. For a quarter of the record, it would be guessing at pictures of pages, or reading text that is simply wrong.");

  // 5. Problems 1-2
  s = pres.addSlide({ masterName: "Content", sectionTitle: "The problems" });
  s.addText("Many documents are photos of paper", { placeholder: "title" });
  await iconRow(s, fa.FaImage, 1.7, "Pictures, not text", "Printed and scanned documents arrive as photos of pages. A computer sees no words until software guesses them.");
  await iconRow(s, fa.FaEyeSlash, 3.0, "What the computer reads is not what you see", "Some scans carry hidden text that does not match the page. An AI tool would quote the hidden text, not what a reviewer reads.");
  await iconRow(s, fa.FaStamp, 4.3, "Files that look searchable but are not", "A copier stamp like “Received” makes a scanned file look searchable while it holds only a few words.");
  T(s, "In plain terms: the same document can say one thing to a person and another to a machine.", { x: 0.6, y: 5.8, w: 12.1, h: 0.6, fontSize: 18, italic: true, color: C.text2 });
  s.addNotes("The first problem is that many documents are not really documents to a computer - they are photos of paper. When someone prints a document and scans it back, the computer sees a picture, not words, and software has to guess what it says. Worse, some scans carry hidden text that does not match the page, so an AI tool would quote something different from what a reviewer reads. And some files look searchable because a copier stamped a word or two on them, while the rest is still a picture.");

  // 6. AI makes things up
  s = pres.addSlide({ masterName: "Content", sectionTitle: "The problems" });
  s.addText("When AI has to guess, it makes things up", { placeholder: "title" });
  T(s, "An AI tool reading a scanned contact list", { x: 0.6, y: 1.55, w: 7.2, h: 0.45, fontSize: 18, bold: true, color: C.accent1 });
  const hdr = { bold: true, color: C.background1, fill: { color: C.text2 } };
  s.addTable([
    [{ text: "", options: hdr }, { text: "On the page", options: hdr }, { text: "What the AI wrote", options: hdr }],
    ["Street number", "980", "680"],
    ["Postal code", "V6Z 0C8", "V5Z OCB"],
    ["E-mail", "a real person", "an invented address"],
    ["Recipients", "two people", "a third who does not exist"],
  ], { x: 0.6, y: 2.1, w: 7.2, colW: [2.0, 2.2, 3.0], fontSize: 16, color: C.text1, border: { type: "solid", pt: 0.75, color: HEX.accent5 }, rowH: 0.55, margin: 0.08 });
  card(s, 8.3, 1.6, 4.4, 4.2, true);
  T(s, "Nothing in its answer said it was guessing", { x: 8.6, y: 1.9, w: 3.8, h: 1.2, fontSize: 22, bold: true, color: C.background1, valign: "top" });
  T(s, "A missing value can be spotted. An invented one looks exactly like a real one — in a permit condition, a test result or a landowner’s address.", { x: 8.6, y: 3.2, w: 3.8, h: 2.4, fontSize: 16, color: C.background2, valign: "top" });
  T(s, "In tests, even the best AI tool got about one number in twenty wrong when reading pictures of pages.", { x: 0.6, y: 5.25, w: 7.2, h: 0.8, fontSize: 16, color: C.text1, valign: "top" });
  s.addNotes("This is the risk that matters most for AI. When an AI tool has to read a picture of a page, it does not leave a blank where it is unsure - it writes something plausible. Reading a scanned contact list, one tool changed a street number, rewrote a postal code, replaced a real e-mail address with an invented one and added a recipient who does not exist. Nothing in its answer said it was guessing. In our tests even the best tool got about one number in twenty wrong. A missing value can be spotted; an invented one looks exactly like the real thing. (Real names withheld.)");

  // 7. Problems 4-5
  s = pres.addSlide({ masterName: "Content", sectionTitle: "The problems" });
  s.addText("Privacy and proof are at risk too", { placeholder: "title" });
  card(s, 0.6, 1.6, 5.9, 4.8, false);
  s.addImage({ data: await icon(fa.FaUserSecret, HEX.accent1), x: 0.95, y: 1.95, w: 0.8, h: 0.8 });
  T(s, "Black boxes that hide nothing", { x: 2.0, y: 2.0, w: 4.3, h: 0.8, fontSize: 22, bold: true, color: C.text2, valign: "middle" });
  T(s, "Some documents black out names with a box drawn on top. The words are still in the file — a computer reads them as if the box were not there. We found landowners’ names and addresses this way.", { x: 0.95, y: 3.0, w: 5.2, h: 3.0, fontSize: 17, color: C.text1, valign: "top" });
  card(s, 6.8, 1.6, 5.9, 4.8, false);
  s.addImage({ data: await icon(fa.FaFingerprint, HEX.dk2), x: 7.15, y: 1.95, w: 0.8, h: 0.8 });
  T(s, "No way to prove a file is the original", { x: 8.2, y: 2.0, w: 4.3, h: 0.8, fontSize: 22, bold: true, color: C.text2, valign: "middle" });
  T(s, "Our record carries no digital fingerprint for any file, and the official signature is still a paper receipt sent by mail. Fewer than 1 document in 100 is signed digitally.", { x: 7.15, y: 3.0, w: 5.2, h: 3.0, fontSize: 17, color: C.text1, valign: "top" });
  s.addNotes("Two more problems. First, privacy: some documents black out personal information by drawing a box over it. The words are still in the file, and any computer - including an AI tool - reads them as if the box were not there. We found landowners' names and addresses exposed this way, and those documents need review now. Second, proof: nothing in our record lets anyone prove a downloaded file is the one that was filed. There is no digital fingerprint, and the official signature is still a paper receipt sent by mail. Fewer than one document in a hundred is signed digitally.");

  // 8. Why it happens
  s = pres.addSlide({ masterName: "Content", sectionTitle: "The problems" });
  s.addText("Why it happens: habits from the paper era", { placeholder: "title" });
  const steps = [[fa.FaFileAlt, "Written on a computer"], [fa.FaPrint, "Printed to sign"], [fa.FaPenNib, "Signed in ink"], [fa.FaCopy, "Scanned back"], [fa.FaImage, "Filed as a picture"]];
  for (let i = 0; i < steps.length; i++) {
    const x = 0.6 + i * 2.5;
    s.addShape(pres.shapes.OVAL, { x: x + 0.5, y: 1.8, w: 1.1, h: 1.1, fill: { color: i === 4 ? C.accent1 : C.text2 }, line: { type: "none" } });
    s.addImage({ data: await icon(steps[i][0], HEX.lt1), x: x + 0.78, y: 2.08, w: 0.55, h: 0.55 });
    T(s, steps[i][1], { x, y: 3.05, w: 2.1, h: 0.7, fontSize: 16, bold: true, color: C.text2, align: "center", valign: "top" });
    if (i < steps.length - 1) s.addShape(pres.shapes.RIGHT_ARROW, { x: x + 1.75, y: 2.2, w: 0.55, h: 0.3, fill: { color: C.accent5 }, line: { type: "none" } });
  }
  card(s, 0.6, 4.2, 12.1, 2.2, false);
  T(s, "Nearly half of the unreadable documents are letters and sworn statements printed just to be signed. Tables and maps are filed as pictures too: our filing system accepts only PDFs, and map data still travels by e-mail or USB stick.", { x: 0.95, y: 4.45, w: 11.4, h: 1.8, fontSize: 18, color: C.text1, valign: "top" });
  s.addNotes("Why does this happen? Mostly habit. A letter or an affidavit is written on a computer, printed only so it can be signed in ink, scanned back, and filed as a picture. Nearly half of the unreadable documents come from exactly this. Data has the same problem: our filing system accepts only PDFs, so tables of test results arrive as pictures inside documents, and map data still travels by e-mail or on a USB stick. None of this is anyone's fault - it is how the paper era taught us to work.");

  // 9. Solution 1: filings
  pres.addSection({ title: "The solution" });
  s = pres.addSlide({ masterName: "Content", sectionTitle: "The solution" });
  s.addText("Ask for files a computer can read", { placeholder: "title" });
  await iconRow(s, fa.FaFileExport, 1.6, "Save as PDF — do not print and scan", "Filers keep their documents digital from start to finish.");
  await iconRow(s, fa.FaSignature, 2.65, "Sign digitally", "Including sworn statements, so signing no longer turns documents into pictures.");
  await iconRow(s, fa.FaTable, 3.7, "Send data as data", "Tables as spreadsheets and maps as map files, alongside the document.");
  await iconRow(s, fa.FaUserShield, 4.75, "Redact properly", "Use tools that remove the words, and say what was withheld.");
  await iconRow(s, fa.FaUniversalAccess, 5.8, "Turn on accessibility tagging", "The same setting that helps screen readers tells a computer what is a heading or a table.");
  s.addNotes("The first part of the solution is what we ask of filers - mostly settings and habits, not new work. Save documents as PDF from the software that made them instead of printing and scanning. Sign digitally, including sworn statements. Send tables as spreadsheets and maps as map files alongside the document. Redact with tools that actually remove the words. And turn on the accessibility tagging most office software already offers - the same setting that helps blind readers also tells a computer what is a heading and what is a table. The CER's review of its Rules of Practice and Procedure, which already lists modernizing electronic filing, is the natural place to make these changes.");

  // 10. Solution 2: portal
  s = pres.addSlide({ masterName: "Content", sectionTitle: "The solution" });
  s.addText("Check and protect every file at the portal", { placeholder: "title" });
  const portal = [
    [fa.FaCheckCircle, "Check every file on upload", "Tell the filer “page 47 has no readable text” while they can still fix it. A rule beats advice."],
    [fa.FaFingerprint, "Fingerprint and time-stamp every file", "Anyone can prove a copy is the original — no paper receipt needed."],
    [fa.FaFileUpload, "Accept spreadsheets, maps and digital signatures", "Not only PDFs, so data stops travelling as pictures, e-mails and USB sticks."],
    [fa.FaCloudDownloadAlt, "Open the record to automation", "Bulk access with each file’s details, so our own tools — and the public’s — can use it."],
  ];
  for (let i = 0; i < portal.length; i++) {
    const x = 0.6 + (i % 2) * 6.2, y = 1.6 + Math.floor(i / 2) * 2.55;
    card(s, x, y, 5.9, 2.3, i === 0);
    s.addImage({ data: await icon(portal[i][0], i === 0 ? HEX.accent1 : HEX.dk2), x: x + 0.3, y: y + 0.3, w: 0.6, h: 0.6 });
    T(s, portal[i][1], { x: x + 1.1, y: y + 0.25, w: 4.5, h: 0.75, fontSize: 19, bold: true, color: i === 0 ? C.background1 : C.text2, valign: "middle" });
    T(s, portal[i][2], { x: x + 0.3, y: y + 1.1, w: 5.3, h: 1.0, fontSize: 15, color: i === 0 ? C.background2 : C.text1, valign: "top" });
  }
  s.addNotes("The second part is our own portal. First and most important, check every file automatically when it is uploaded, and tell the filer exactly what is wrong while they can still fix it - a rule built into the system works where advice in a manual does not. Second, fingerprint and time-stamp every file so anyone can prove a copy is the original without a paper receipt. Third, accept spreadsheets, map files and digital signatures, not just PDFs. Fourth, open the record to automation with bulk access, so our own AI tools, and the public's, can use it.");

  // 11. Solution 3: build the capability
  s = pres.addSlide({ masterName: "Content", sectionTitle: "The solution" });
  s.addText("Build an AI team and a cloud strategy", { placeholder: "title" });
  const roles = [
    [fa.FaCompass, "Business owner", "sets priorities and measures value"],
    [fa.FaBalanceScale, "Regulatory experts", "define what “right” looks like"],
    [fa.FaDatabase, "Data engineers", "turn the record into reliable data"],
    [fa.FaRobot, "AI engineers", "build, test and monitor AI tools"],
    [fa.FaCloud, "Cloud and security", "a secure, scalable platform"],
    [fa.FaArchive, "Information management", "records, fingerprints, retention"],
    [fa.FaGavel, "Privacy, legal, ethics", "responsible-AI rules and reviews"],
    [fa.FaChalkboardTeacher, "Change and training", "help staff and filers adopt it"],
  ];
  for (let i = 0; i < roles.length; i++) {
    const x = 0.6 + (i % 4) * 3.05, y = 1.55 + Math.floor(i / 4) * 1.85;
    card(s, x, y, 2.85, 1.65, false);
    s.addImage({ data: await icon(roles[i][0], HEX.dk2), x: x + 0.2, y: y + 0.22, w: 0.45, h: 0.45 });
    T(s, roles[i][1], { x: x + 0.8, y: y + 0.15, w: 1.95, h: 0.6, fontSize: 15, bold: true, color: C.text2, valign: "middle" });
    T(s, roles[i][2], { x: x + 0.2, y: y + 0.85, w: 2.5, h: 0.7, fontSize: 13, color: C.text1, valign: "top" });
  }
  T(s, "Others are already moving", { x: 0.6, y: 5.35, w: 12.1, h: 0.4, fontSize: 16, bold: true, color: C.accent1 });
  T(s, [
    { text: "BC Energy Regulator", options: { bold: true } }, { text: " is expanding an AI permit assistant on its cloud platform (2026).   " },
    { text: "Alberta Energy Regulator", options: { bold: true } }, { text: " built OneStop: most routine applications approved automatically.   " },
    { text: "Government of Canada", options: { bold: true } }, { text: " AI Strategy 2025–27: an AI Centre of Expertise and responsible-AI rules." },
  ], { x: 0.6, y: 5.75, w: 12.1, h: 1.0, fontSize: 13, color: C.text1, valign: "top" });
  s.addNotes("The third part is the capability to do this well. AI is a team effort, not a tool you buy. A typical AI program needs a business owner who sets priorities and measures value; regulatory experts who define what a right answer looks like; data engineers who turn the record into reliable data; AI engineers who build, test and monitor the tools; a cloud and security team for a secure, scalable platform; information management for records and fingerprints; privacy, legal and ethics under the federal rules on automated decision-making; and change and training to bring staff and filers along. Others are already moving. The BC Energy Regulator is expanding an AI permit assistant on its cloud platform (CanadaBuys tender, July 2026). The Alberta Energy Regulator's OneStop portal approved about 70 per cent of reclamation, pipeline and water applications automatically (AER 2017/18 annual report, as reported by IT World Canada). The federal AI Strategy for the Public Service 2025-2027 creates an AI Centre of Expertise, sets responsible-AI expectations, and notes a 30 per cent vacancy rate in digital roles - so talent is the constraint to plan for.");

  // 12. Lessons + decisions
  pres.addSection({ title: "Making change happen" });
  s = pres.addSlide({ masterName: "Statement Dark", sectionTitle: "Making change happen" });
  s.addText("Lessons for every part of the organization", { placeholder: "title" });
  const lessons = [
    ["AI is only as good as its data", "Fix data where it is created, not after."],
    ["Failures are silent", "A system can look finished and be wrong. Ask for proof."],
    ["Rules beat advice", "Build checks into systems instead of writing guidance."],
    ["Small habits add up", "Printing to sign made nearly half the problem."],
  ];
  for (let i = 0; i < lessons.length; i++) {
    const x = 0.6 + (i % 2) * 6.2, y = 1.55 + Math.floor(i / 2) * 1.35;
    T(s, lessons[i][0], { x, y, w: 5.9, h: 0.55, fontSize: 21, bold: true, color: C.accent1 });
    T(s, lessons[i][1], { x, y: y + 0.55, w: 5.9, h: 0.6, fontSize: 16, color: C.background2, valign: "top" });
  }
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: 0.6, y: 4.35, w: 12.1, h: 2.4, fill: { color: C.background1 }, line: { type: "none" }, rectRadius: 0.15 });
  T(s, "What we are asking for", { x: 0.95, y: 4.55, w: 11.4, h: 0.5, fontSize: 20, bold: true, color: C.text2 });
  T(s, [
    { text: "Stand up an AI build team and a cloud strategy, with an executive sponsor", options: { bullet: true, breakLine: true } },
    { text: "Change our filing rules and portal through the Rules of Practice review already under way", options: { bullet: true, breakLine: true } },
    { text: "Start now with two quick wins: the automatic upload check, and a privacy review of flawed redactions", options: { bullet: true } },
  ], { x: 0.95, y: 5.05, w: 11.4, h: 1.6, fontSize: 17, color: C.text1, paraSpaceAfter: 6, valign: "top" });
  s.addNotes("These lessons apply well beyond filings. AI is only as good as its data, so fix data where it is created. Failures are silent - a system can look finished and still be wrong, so ask for proof. Rules beat advice - build checks into systems rather than writing more guidance. And small habits add up - printing documents just to sign them created nearly half of this problem. What we are asking for: stand up an AI build team and a cloud strategy with an executive sponsor; change our filing rules and portal through the Rules of Practice review that is already under way; and start now with two quick wins - the automatic upload check, and a privacy review of the documents whose redactions do not hide anything.");

  await pres.writeFile({ fileName: OUT });
  await applyTheme(OUT, THEME);
  console.log("wrote", OUT);
})();
