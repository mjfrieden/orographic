import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { accountReturnsHtml } from '../../web/account-returns.js';
const css = readFileSync(new URL('../../web/evidence-workbench.css', import.meta.url), 'utf8');
const selector = '.evidence-workbench a[href="/account-return-receipts.html"]';
const rule = (selectors) => {
 const start = css.indexOf(`${selectors}{`); assert.ok(start >= 0, `Missing scoped rule ${selectors}`);
 return css.slice(start + selectors.length + 1, css.indexOf('}', start));
};
const luminance = (hex) => {
 const rgb = hex.match(/[a-f\d]{2}/gi).map(v => parseInt(v, 16) / 255).map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4);
 return rgb[0] * .2126 + rgb[1] * .7152 + rgb[2] * .0722;
};
const contrast = (a, b) => (Math.max(luminance(a), luminance(b)) + .05) / (Math.min(luminance(a), luminance(b)) + .05);
test('receipt help remains an ordinary link matching the scoped style', () => {
 const html = accountReturnsHtml({}, { authenticated: true, session: { role: 'admin' } }, { accountId: 'TEST', mode: 'sandbox' });
 assert.match(html, /<a href="\/account-return-receipts\.html" target="_blank" rel="noopener">Receipt format and limitations<\/a>/);
});
test('normal and visited receipt links use gold and permanent underline', () => {
 const declarations = rule(`${selector},${selector}:visited`);
 assert.match(declarations, /color:#f4dda4;/); assert.match(declarations, /text-decoration:underline;/); assert.match(declarations, /text-underline-offset:3px/);
});
test('hover and keyboard focus use teal without removing existing outline', () => {
 assert.match(rule(`${selector}:hover,${selector}:focus-visible`), /color:#73e1d7;text-decoration-thickness:2px/);
 assert.match(css, /\.evidence-workbench a:focus-visible\{outline:2px solid #73e1d7;outline-offset:4px\}/);
});
test('link-state palette exceeds normal-text contrast on conservative panel background', () => {
 // Brighter than the current rgba(7,17,30,.95) panel plus gold tint, even composited on white.
 for (const color of ['f4dda4', '73e1d7']) assert.ok(contrast(color, '27374a') >= 4.5, `${color} contrast ${contrast(color, '27374a')}`);
});
