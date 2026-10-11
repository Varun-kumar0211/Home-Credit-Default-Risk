import re
import pathlib

def check_module(path):
    src = pathlib.Path(path).read_text(encoding='utf-8')
    exported = re.findall(r'export\s+(?:async\s+)?function\s+(\w+)', src)
    exported += re.findall(r'export\s+(?:const|let|var)\s+(\w+)', src)
    print(f'\n--- {path} ---')
    print(f'  Exports found: {sorted(set(exported))}')
    return src

# admin.js
admin = check_module('Frontend/js/admin.js')
assert 'export async function loadAdminOverview' in admin, 'loadAdminOverview not exported from admin.js'
print('  [OK] loadAdminOverview exported')

# api.js
api = check_module('Frontend/js/api.js')
assert 'getAdminOverview' in api, 'getAdminOverview not in api.js'
print('  [OK] getAdminOverview present')

# main.js
main = check_module('Frontend/js/main.js')
assert './admin.js' in main, 'admin.js not imported in main.js'
assert 'loadAdminOverview' in main, 'loadAdminOverview not used in main.js'
print('  [OK] admin.js imported and loadAdminOverview called')

# reviewer.js
rev = check_module('Frontend/js/reviewer.js')
for fn in ['loadAssignedApplications', 'loadPendingReviews', 'loadReviewHistory',
           'renderFinancialInfo', 'renderPredictionInfo', 'renderDecisionActions',
           'renderShapInfo', 'clearReviewerState', 'setReviewerNavigator']:
    assert fn in rev, f'{fn} not found in reviewer.js'
print('  [OK] All reviewer.js exports present')

# views.js
views = check_module('Frontend/js/views.js')
for fn in ['assessManual', 'loadDefault', 'loadLoanApplications', 'loadReviewers', 'upload']:
    assert fn in views, f'{fn} not found in views.js'
print('  [OK] views.js key exports present')

print()
print('All module checks PASSED')
