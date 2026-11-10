/* Reorganise the catalogue's device/program rules without changing their contents or IDs. */
(function () {
  'use strict';

  var GROUPS = [
    {
      id: 'rules-static',
      title: 'Static & occupancy',
      blurb: 'What may exist or be occupied at an instant: setup, capacities of sites, rails and junctions, and the machine\u2019s topology and geometry.',
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
    },
    {
      id: 'rules-c2lr',
      title: 'Swap-or-stay lattices (C2LR)',
      blurb: 'What a C2LR step may do: one direction bit per leg, one shared waveform, crystals ending at leg centres, and the control it takes. They judge only a C2LR machine.',
      rules: ['C1', 'C2', 'C3', 'C4', 'C5', 'C6', 'C7', 'C8', 'C9', 'C10']
    },
    {
      id: 'rules-wise',
      title: 'Parallel switched swaps (WISE)',
      blurb: 'What a WISE chip may do: one parity and one kind of operation across the chip in a step, one gate at one angle in a layer, and a shim charge before every layer.',
      rules: ['W1', 'W2', 'W4', 'W10', 'W11']
    },
    {
      id: 'rules-cyclone',
      title: 'Ring codesign (Cyclone)',
      blurb: 'What a Cyclone ring may do: one closed ring, the data still, every ancilla one trap on in lockstep, at most 30 ions a trap.',
      rules: ['Y1', 'Y2', 'Y3', 'Y4']
    }
  ];
  var ARCHITECTURES = window.QCCD_RULE_PROFILES || [];

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
    if (ARCHITECTURES.length) {
      var architecture = document.createElement('a');
      architecture.href = '#architecture-overlays';
      architecture.textContent = 'Architecture rule profiles';
      architecture.title = 'Architecture rule profiles';
      architecture.className = 'tc-2 rule-category-toc';
      cursor.after(architecture);
      cursor = architecture;
      ARCHITECTURES.forEach(function (profile) {
        var link = document.createElement('a');
        link.href = '#architecture-' + profile.id;
        link.textContent = profile.title;
        link.title = profile.title;
        cursor.after(link);
        cursor = link;
      });
    }
  }

  function ruleLinks(rules) {
    var span = document.createElement('span');
    (rules || []).forEach(function (rule, i) {
      if (i) span.appendChild(document.createTextNode(', '));
      span.appendChild(linkFor(rule));
    });
    return span;
  }

  function architectureFlow(profile) {
    var visual = profile.visual;
    if (!visual || !visual.steps || !visual.steps.length) return null;
    var figure = document.createElement('figure');
    figure.className = 'architecture-flow';
    figure.setAttribute('aria-label', visual.title);
    var caption = document.createElement('figcaption');
    caption.textContent = visual.title;
    figure.appendChild(caption);
    var steps = document.createElement('ol');
    visual.steps.forEach(function (step) {
      var item = document.createElement('li');
      var label = document.createElement('strong');
      label.textContent = step.label;
      var detail = document.createElement('span');
      detail.textContent = step.detail;
      item.appendChild(label);
      item.appendChild(detail);
      steps.appendChild(item);
    });
    figure.appendChild(steps);
    return figure;
  }

  function architectureOverlays() {
    if (!ARCHITECTURES.length) return null;
    var section = document.createElement('section');
    section.id = 'architecture-overlays';
    section.className = 'architecture-overlays';
    var head = document.createElement('header');
    var h = document.createElement('h3');
    h.textContent = 'Architecture rule profiles';
    var intro = document.createElement('p');
    intro.textContent = 'Each profile binds a real physical machine to the exact rule set the checker runs. The lists below separate architecture-only checks from general rules that stay, strengthen, or step aside in one narrowly defined phase.';
    head.appendChild(h);
    head.appendChild(intro);
    section.appendChild(head);

    var grid = document.createElement('div');
    grid.className = 'architecture-profile-grid';
    ARCHITECTURES.forEach(function (profile) {
      var card = document.createElement('article');
      card.id = 'architecture-' + profile.id;
      card.className = 'architecture-profile';
      card.dataset.architecture = profile.id;
      var title = document.createElement('h4');
      title.textContent = profile.title;
      var scope = document.createElement('p');
      scope.className = 'architecture-scope';
      scope.textContent = profile.scope;
      var source = document.createElement('a');
      source.className = 'architecture-source';
      source.href = profile.source_url;
      source.textContent = profile.source;
      card.appendChild(title);
      card.appendChild(scope);
      card.appendChild(source);

      var machine = document.createElement('div');
      machine.className = 'architecture-machine';
      var machineTitle = document.createElement('strong');
      machineTitle.textContent = 'Physical machine · ' + profile.architecture.label;
      var facts = document.createElement('ul');
      facts.className = 'architecture-facts';
      profile.architecture.facts.forEach(function (fact) {
        var factItem = document.createElement('li');
        factItem.textContent = fact;
        facts.appendChild(factItem);
      });
      var ruleSet = document.createElement('p');
      ruleSet.className = 'architecture-rule-set';
      ruleSet.appendChild(document.createTextNode('Checker profile '));
      var ruleSetName = document.createElement('code');
      ruleSetName.textContent = profile.ruleset;
      ruleSet.appendChild(ruleSetName);
      ruleSet.appendChild(document.createTextNode(' · ' + profile.ruleset_title));
      machine.appendChild(machineTitle);
      machine.appendChild(facts);
      machine.appendChild(ruleSet);
      card.appendChild(machine);
      var flow = architectureFlow(profile);
      if (flow) card.appendChild(flow);

      var addTitle = document.createElement('h5');
      addTitle.textContent = profile.rule_label;
      card.appendChild(addTitle);
      var additions = document.createElement('ol');
      additions.className = 'architecture-additions';
      profile.rules.forEach(function (addition) {
        var item = document.createElement('li');
        var label = document.createElement('strong');
        label.appendChild(linkFor(addition.id));
        label.appendChild(document.createTextNode(' · ' + addition.title));
        var statement = document.createElement('span');
        statement.textContent = addition.statement;
        item.appendChild(label);
        item.appendChild(statement);
        additions.appendChild(item);
      });
      card.appendChild(additions);

      var suppressTitle = document.createElement('h5');
      suppressTitle.textContent = 'Rule changes and scoped exceptions';
      card.appendChild(suppressTitle);
      var suppressions = document.createElement('ul');
      suppressions.className = 'architecture-suppressions';
      profile.changes.forEach(function (suppression) {
        var item = document.createElement('li');
        var label = document.createElement('strong');
        label.appendChild(document.createTextNode(suppression.kind + ' · '));
        label.appendChild(ruleLinks(suppression.rules));
        label.appendChild(document.createTextNode(' — ' + suppression.when));
        var reason = document.createElement('span');
        reason.textContent = suppression.reason;
        item.appendChild(label);
        item.appendChild(reason);
        suppressions.appendChild(item);
      });
      card.appendChild(suppressions);

      var verification = document.createElement('div');
      verification.className = 'architecture-verification';
      var verificationTitle = document.createElement('strong');
      verificationTitle.textContent = 'Tested behaviour';
      var verificationList = document.createElement('ul');
      profile.verification.forEach(function (check) {
        var checkItem = document.createElement('li');
        checkItem.textContent = check;
        verificationList.appendChild(checkItem);
      });
      verification.appendChild(verificationTitle);
      verification.appendChild(verificationList);
      card.appendChild(verification);
      grid.appendChild(card);
    });
    section.appendChild(grid);
    return section;
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

    var overlays = architectureOverlays();
    if (overlays) nav.parentNode.insertBefore(overlays, nav.nextSibling);
    var cursor = overlays || nav;
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
