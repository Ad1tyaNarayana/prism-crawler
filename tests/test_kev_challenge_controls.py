from playwright.async_api import async_playwright

from experiments.kev_challenges import decision_payload, observe


async def test_browser_offers_only_enabled_unchecked_controls_in_dom_and_shadow():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.set_content('''<title>Please wait</title><p>Verification in progress</p>
          <label><input type="checkbox" checked>Verify checked</label>
          <label><input type="checkbox" disabled>Verify disabled</label>
          <label hidden><input type="checkbox">Verify hidden</label>
          <label><input type="checkbox">Verify ready</label>
          <div id="shadow"></div><script>
          const root=document.querySelector('#shadow').attachShadow({mode:'closed'});
          root.innerHTML='<label><input type="checkbox" disabled>Verify shadow disabled</label>'+
           '<label><input type="checkbox" checked>Verify shadow checked</label>'+
           '<label><input type="checkbox">Verify shadow ready</label>';
          </script>''')
        state, handles = await observe(page)
        assert {c['label'] for c in state['controls']} == {'Verify ready', 'Verify shadow ready'}
        assert len(handles) == 2
        payload = decision_payload(state)
        assert 'Verification in progress' not in payload['state']
        assert 'Verify shadow ready' in payload['state']
        assert 'WAIT' in payload['questions']['action']['criteria']
        for c in state['controls']:
            kind, target = handles[c['id']]
            if kind == 'dom':
                await target.check()
            else:
                await page.mouse.click(**target)
        after, _ = await observe(page)
        assert not after['controls']
        assert 'Verification in progress' in decision_payload(after)['state']
        assert not any(k.startswith('CLICK_') for k in decision_payload(after)['questions']['action']['criteria'])
        await browser.close()


def test_unvalidated_control_cannot_become_an_available_model_action():
    state = {'title': 'Please wait', 'frames': [], 'controls': [
        {'id': 'CLICK_0', 'type': 'checkbox', 'label': 'Verify', 'enabled': False, 'checked': False},
        {'id': 'CLICK_1', 'type': 'checkbox', 'label': 'Verify', 'enabled': True, 'checked': True},
        {'id': 'CLICK_2', 'type': 'checkbox', 'label': 'Verify'},
    ]}
    payload = decision_payload(state)
    assert set(payload['questions']['action']['criteria']) == {'WAIT', 'STOP', 'DONE'}
