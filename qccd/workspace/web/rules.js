/* Reorganise the catalogue's device/program rules without changing their contents or IDs. */
(function () {
  'use strict';

  var GROUPS = [
    {
      id: 'rules-static',
      title: 'Static & occupancy',
      blurb: 'What may exist or be occupied at an instant: setup, capacities of sites, rails and junctions, and the device\u2019s topology and geometry.',
      rules: ['R0', 'R1', 'R2', 'R3', 'R13', 'R18', 'R19', 'R20', 'R21', 'R23']
    },
    {
      id: 'rules-transport',
      title: 'Transport & control',
      blurb: 'How ions may move: declared movement classes, rail ordering, loop direction, split placement and the waveforms that can run together.',
      rules: ['R4', 'R4b', 'R4d', 'R5', 'R11', 'R14', 'R22', 'R31']
    },
    {
      id: 'rules-operation',
      title: 'Operations & timing',
      blurb: 'What may run at a site and when: capabilities, cooling and heating, gate serialization, circuit correctness, ordering and duration.',
      rules: ['R6', 'R6b', 'R7', 'R7b', 'R7c', 'R8', 'R9', 'R10', 'R12', 'R15', 'R16', 'R17',
              'R24', 'R25', 'R26', 'R27', 'R29', 'R30', 'R32', 'R33']
    }
  ];

  function linkFor(rule) {
    var a = document.createElement('a');
    a.href = '#' + rule;
    a.textContent = rule;
    return a;
  }

  function organiseSidebar() {
    var toc = document.getElementById('sitetoc');
    var allLink = toc && toc.querySelector('a[href="#all"]');
    if (!allLink || toc.querySelector('.rule-category-toc')) return;
    var cursor = allLink;
    GROUPS.forEach(function (group) {
      var category = document.createElement('a');
      category.href = '#' + group.id;
      category.textContent = group.title;
      category.title = group.title;
      category.className = 'tc-2 rule-category-toc';
      cursor.after(category);
      cursor = category;
      group.rules.forEach(function (rule) {
        var link = toc.querySelector('a[href="#' + rule + '"]');
        if (!link) return;
        cursor.after(link);
        cursor = link;
      });
    });
  }

  function organise() {
    var all = document.getElementById('all');
    if (!all || document.getElementById('rule-categories')) return;

    var intro = all.nextElementSibling;
    var oldToc = intro && intro.nextElementSibling;
    var nav = document.createElement('nav');
    nav.id = 'rule-categories';
    nav.setAttribute('aria-label', 'Rule categories');

    GROUPS.forEach(function (group) {
      var card = document.createElement('a');
      card.className = 'rule-category-card';
      card.href = '#' + group.id;
      var title = document.createElement('strong');
      title.textContent = group.title;
      var count = document.createElement('span');
      count.textContent = group.rules.length + ' rules';
      var blurb = document.createElement('small');
      blurb.textContent = group.blurb;
      card.appendChild(title);
      card.appendChild(count);
      card.appendChild(blurb);
      nav.appendChild(card);
    });
    all.parentNode.insertBefore(nav, oldToc ? oldToc.nextSibling : all.nextSibling);

    var cursor = nav;
    GROUPS.forEach(function (group) {
      var section = document.createElement('section');
      section.className = 'rule-category';
      section.id = group.id;
      section.setAttribute('aria-labelledby', group.id + '-title');

      var head = document.createElement('header');
      var h = document.createElement('h3');
      h.id = group.id + '-title';
      h.textContent = group.title;
      var p = document.createElement('p');
      p.textContent = group.blurb;
      var chips = document.createElement('div');
      chips.className = 'rule-category-links';
      group.rules.forEach(function (rule) { chips.appendChild(linkFor(rule)); });
      head.appendChild(h);
      head.appendChild(p);
      head.appendChild(chips);
      section.appendChild(head);

      group.rules.forEach(function (rule) {
        var ruleCard = document.getElementById(rule);
        if (!ruleCard || !ruleCard.classList.contains('rule')) return;
        ruleCard.dataset.category = group.id.replace('rules-', '');
        section.appendChild(ruleCard);
      });
      cursor.parentNode.insertBefore(section, cursor.nextSibling);
      cursor = section;
    });

    if (oldToc && oldToc.classList.contains('toc2')) oldToc.hidden = true;
    /* The site's sticky index is assembled at DOM ready too; let its handler finish first. */
    requestAnimationFrame(organiseSidebar);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', organise);
  else organise();
}());
