"""Exercise the additive review controls against a minimal offline DOM adapter."""

# ruff: noqa: E501 - embedded offline JavaScript test adapter.
import shutil
import subprocess

import pytest

from linkedin_automation.interfaces.lead_review_filters import SCRIPT


def test_newest_sort_brave_shortcut_and_preserved_underlying_leads():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for dashboard script behavior checks")
    harness = r"""
const vm=require('node:vm'),assert=require('node:assert/strict'),registry={};
function element(tag){const e={children:[],value:'',style:{},open:false,
 append(...items){this.children.push(...items)},add(option){if(!this.children.length)this.value=option.value;this.children.push(option)},
 setAttribute(){},after(){},scrollIntoView(){},querySelector(){return {append(){}}}};
 Object.defineProperty(e,'id',{get(){return this._id},set(value){this._id=value;registry[value]=this}});return e;}
for(const id of ['search','statusFilter','leadRows','editor']){const e=element('div');e.id=id;}
const toolbar=element('div');registry.search.closest=()=>toolbar;
const old={lead:{id:'old',full_name:'Earlier Approved',source_search:'LinkedIn',last_observed_at:'2026-10-01',company:'Existing',current_title:'Founder'},outreach:{score:90,status:'approved'}};
const luke={lead:{id:'luke',full_name:'Luke Carter',source_search:'Brave public web index: family office',last_observed_at:'2026-11-06',headline:'Founder'},outreach:{score:40,status:'needs_verification'}};
let shown=[];
const context={document:{getElementById:id=>registry[id],createElement:element,visibilityState:'visible'},
 Option:function(text,value){this.text=text;this.value=value},leads:[old,luke],
 setInterval(){},load:async()=>{},loadSummary:async()=>{},notice(){}};
context.render=()=>{const q=registry.search.value.toLowerCase(),status=registry.statusFilter.value;
 shown=context.leads.filter(item=>(!q||item.lead.full_name.toLowerCase().includes(q))&&(!status||item.outreach.status===status)).map(item=>item.lead.id);
 registry.leadRows.children=shown.map(()=>element('tr'));};
vm.createContext(context);vm.runInContext(process.argv[1],context);
assert.deepEqual(shown,['luke','old']); // newest, not highest-score, first
assert.deepEqual(context.leads,[old,luke]); // rendering cannot change underlying data
registry.leadReviewSort.value='score';registry.leadReviewSort.onchange();assert.deepEqual(shown,['old','luke']);
registry.search.value='Earlier';registry.statusFilter.value='approved';
registry.reviewBraveLeads.onclick();assert.deepEqual(shown,['luke']);
assert.equal(registry.search.value,'');assert.equal(registry.statusFilter.value,'');
assert.equal(registry.leadSourceFilter.value,'brave');
assert.equal(luke.outreach.score,40);assert.equal(old.outreach.status,'approved');
assert.match(registry.savedReviewSummary.textContent,/Showing 1 of 2/);
assert.match(registry.reviewBraveLeads.textContent,/\(1\)/);
"""
    result = subprocess.run(
        [node, "-e", harness, SCRIPT.removeprefix("<script>").removesuffix("</script>")],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
