// Rich text editor module — Tiptap with tables, lists, images, code blocks.
// Loaded as <script type="module"> from index.html.
//
// Usage:
//   const editor = window.RichEditor.create(mountDiv, {
//       content: 'plain text or HTML or Tiptap JSON',
//       placeholder: 'Type your answer…',
//       onUpdate: (editor) => { ... },
//       onBlur:   (editor) => { ... },
//       onFocus:  (editor) => { ... },
//       onImagePaste: (file, editor) => { ... },  // intercept image paste
//   });
//   editor.getText();      // serialise to markdown-style plain text (for Claude / search)
//   editor.getJSON();      // Tiptap's native JSON (for persistence)
//   editor.getHTML();      // HTML (for PDF rendering)
//
// CDN: esm.sh handles dependency dedup. Initial load fetches ~100 KB gzipped;
// subsequent loads hit the browser cache.

import { Editor, Extension } from 'https://esm.sh/@tiptap/core@2.10.4';
import StarterKit       from 'https://esm.sh/@tiptap/starter-kit@2.10.4';
import Table            from 'https://esm.sh/@tiptap/extension-table@2.10.4';
import TableRow         from 'https://esm.sh/@tiptap/extension-table-row@2.10.4';
import TableHeader      from 'https://esm.sh/@tiptap/extension-table-header@2.10.4';
import TableCell        from 'https://esm.sh/@tiptap/extension-table-cell@2.10.4';
import ImageExt         from 'https://esm.sh/@tiptap/extension-image@2.10.4';
import Placeholder      from 'https://esm.sh/@tiptap/extension-placeholder@2.10.4';

// ---------- Custom extensions ----------
//
// TabHandler: stop Tab from moving focus out of the editor when the cursor
// isn't inside a table. Tiptap's table extension already handles Tab inside
// table cells (moves to next cell); this just keeps Tab usable as a real
// tab character everywhere else.
const TabHandler = Extension.create({
    name: 'tabHandler',
    addKeyboardShortcuts() {
        return {
            Tab: () => {
                const { editor } = this;
                if (editor.isActive('table')) return false; // let table ext handle
                return editor.chain().insertContent('\t').run();
            },
            'Shift-Tab': () => {
                const { editor } = this;
                if (editor.isActive('table')) return false; // table ext: prev cell
                // Outside tables, swallow Shift-Tab so it doesn't focus the
                // previous element either; no-op is fine.
                return true;
            },
        };
    },
});

// ---------- Markdown serialiser ----------
// Walks Tiptap JSON and emits markdown-flavoured plain text. This is what
// Claude evaluates and what falls back into the `answer` field if we ever
// need to render in a plain-text context.

function serialiseDoc(node) {
    if (!node) return '';
    if (node.type === 'doc') {
        return (node.content || []).map(serialiseBlock).join('\n\n').trim();
    }
    return serialiseBlock(node);
}

function serialiseBlock(node) {
    if (!node) return '';
    switch (node.type) {
        case 'paragraph':
            return (node.content || []).map(serialiseInline).join('');
        case 'heading': {
            const level = (node.attrs && node.attrs.level) || 1;
            return '#'.repeat(level) + ' ' + (node.content || []).map(serialiseInline).join('');
        }
        case 'bulletList':
            return (node.content || []).map(item => '- ' + serialiseListItem(item)).join('\n');
        case 'orderedList':
            return (node.content || []).map((item, i) => `${i + 1}. ` + serialiseListItem(item)).join('\n');
        case 'listItem':
            return serialiseListItem(node);
        case 'codeBlock': {
            const lang = (node.attrs && node.attrs.language) || '';
            const code = (node.content || []).map(c => c.text || '').join('');
            return '```' + lang + '\n' + code + '\n```';
        }
        case 'blockquote':
            return (node.content || []).map(c => '> ' + serialiseBlock(c)).join('\n');
        case 'table':
            return serialiseTable(node);
        case 'image':
            // Image attrs include src (data URL). For evaluation/serialise we
            // emit a marker — the actual base64 lives in editor JSON & is
            // sent separately when persisting.
            return '[image]';
        case 'horizontalRule':
            return '---';
        case 'hardBreak':
            return '  \n';
        default:
            // Unknown block: recurse into children, hope for the best
            return (node.content || []).map(serialiseBlock).join('\n');
    }
}

function serialiseListItem(item) {
    return (item.content || []).map(c => {
        // Indent nested blocks past the bullet
        const txt = serialiseBlock(c);
        return txt;
    }).join('\n  ');
}

function serialiseInline(node) {
    if (!node) return '';
    if (node.type === 'hardBreak') return '  \n';
    if (node.type !== 'text') return '';
    let s = node.text || '';
    for (const m of (node.marks || [])) {
        if (m.type === 'bold')   s = '**' + s + '**';
        else if (m.type === 'italic') s = '*' + s + '*';
        else if (m.type === 'code')   s = '`' + s + '`';
        else if (m.type === 'strike') s = '~~' + s + '~~';
    }
    return s;
}

function serialiseTable(tableNode) {
    const rows = (tableNode.content || []).filter(r => r.type === 'tableRow');
    if (!rows.length) return '';
    const cells = rows.map(row => (row.content || []).map(cell =>
        (cell.content || []).map(c => serialiseBlock(c)).join(' ').replace(/\n+/g, ' ').trim()
    ));
    const colCount = Math.max(...cells.map(r => r.length));
    cells.forEach(r => { while (r.length < colCount) r.push(''); });
    const header = cells[0];
    const body   = cells.slice(1);
    const sep    = Array(colCount).fill('---');
    return [
        '| ' + header.join(' | ') + ' |',
        '| ' + sep.join(' | ')    + ' |',
        ...body.map(r => '| ' + r.join(' | ') + ' |'),
    ].join('\n');
}

// ---------- Content loading ----------
// Accepts: Tiptap JSON object, HTML string, or plain text. Converts to a
// form Tiptap can render.

function normaliseContent(input) {
    if (!input) return '';
    if (typeof input === 'object') return input;        // JSON
    if (typeof input !== 'string') return '';
    const trimmed = input.trim();
    if (!trimmed) return '';
    // Heuristic: if it starts with `<` treat as HTML; otherwise plain text.
    if (trimmed.startsWith('<')) return trimmed;
    // Plain text: split on blank lines into paragraphs, escape HTML, preserve
    // single newlines as <br>.
    const esc = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    return trimmed.split(/\n{2,}/).map(p =>
        '<p>' + esc(p).replace(/\n/g, '<br>') + '</p>'
    ).join('');
}

// ---------- Public factory ----------

window.RichEditor = {
    create(mountElement, opts = {}) {
        const editor = new Editor({
            element: mountElement,
            extensions: [
                StarterKit.configure({
                    heading: { levels: [1, 2, 3] },
                    codeBlock: { HTMLAttributes: { class: 're-code' } },
                }),
                // Inline border styles on every table node so they're visible
                // regardless of any CSS specificity issues (Tiptap's tables
                // sometimes render inside a `.tableWrapper` div that interferes
                // with descendant selectors).
                Table.configure({
                    resizable: true,
                    HTMLAttributes: {
                        class: 're-table',
                        style: 'border-collapse: collapse; width: 100%; margin: 0.6em 0; border: 2px solid #161616;',
                    },
                }),
                TableRow,
                TableHeader.configure({
                    HTMLAttributes: {
                        style: 'border: 1.5px solid #161616; padding: 0.4em 0.55em; background: #efe6d4; font-weight: 600; text-align: left; vertical-align: top; min-width: 5em;',
                    },
                }),
                TableCell.configure({
                    HTMLAttributes: {
                        style: 'border: 1.5px solid #161616; padding: 0.4em 0.55em; vertical-align: top; min-width: 5em;',
                    },
                }),
                ImageExt.configure({ inline: false, allowBase64: true, HTMLAttributes: { class: 're-image' } }),
                Placeholder.configure({
                    placeholder: opts.placeholder || 'Type your answer…',
                    showOnlyWhenEditable: true,
                    showOnlyCurrent: false,
                }),
                TabHandler,
            ],
            content: normaliseContent(opts.content),
            editorProps: {
                attributes: {
                    class: 're-editor-content answer-textarea',
                    spellcheck: 'true',
                    'data-rich-editor': '1',
                },
                handlePaste(view, event) {
                    if (!opts.onImagePaste) return false;
                    const items = event.clipboardData && event.clipboardData.items;
                    if (!items) return false;
                    for (const it of items) {
                        if (it.kind === 'file' && it.type.startsWith('image/')) {
                            event.preventDefault();
                            const file = it.getAsFile();
                            if (file) opts.onImagePaste(file, editor);
                            return true;
                        }
                    }
                    return false;
                },
            },
            onUpdate: ({ editor: ed }) => { if (opts.onUpdate) opts.onUpdate(ed); },
            onBlur:   ({ editor: ed }) => { if (opts.onBlur)   opts.onBlur(ed); },
            onFocus:  ({ editor: ed }) => { if (opts.onFocus)  opts.onFocus(ed); },
        });

        // Augment with convenient serialisers (no need to import from outside)
        editor.getMarkdown = () => serialiseDoc(editor.getJSON());

        return editor;
    },
};
