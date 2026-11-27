"""Bound native form proposals. Page code never receives an action grant."""

import hashlib
import json
from urllib.parse import urlencode, urlsplit

from utils.public_http import NetworkError, destination


# All form inspection is fixed host code. No values are typed into a live page,
# so input/change/click listeners cannot spend an approval on another request.
FORM_SNAPSHOT = """selector => {
 const matches = document.querySelectorAll(selector);
 if (matches.length !== 1) return null;
 const picked = matches[0];
 const form = picked.tagName === 'FORM' ? picked : picked.form;
 if (!form || form.outerHTML.length > 32000 || form.elements.length > 30) return null;
 if (picked !== form && ((picked.hasAttribute('formaction') && picked.formAction !== form.action) ||
     (picked.hasAttribute('formmethod') && picked.formMethod !== form.method) ||
     (picked.hasAttribute('formenctype') && picked.formEnctype !== form.enctype))) return null;
 const controls = [];
 for (const e of form.elements) {
   if (e.disabled || !e.name) continue;
   if (e.name.length > 100 || (e.value || '').length > 1000) return null;
   const type = (e.type || '').toLowerCase();
   if (['file','password','image'].includes(type)) return null;
   if (['submit','button','reset'].includes(type) && e !== picked) continue;
   if (['checkbox','radio'].includes(type) && !e.checked) continue;
   if (e.tagName === 'SELECT' && e.multiple) return null;
   controls.push({name:e.name, value:e.value || '', type});
 }
 return {page:location.href, action:form.action, method:form.method.toUpperCase(),
   enctype:form.enctype, dom:form.outerHTML, controls};
}"""


def validate_fields(selector, fields):
    if (not isinstance(selector,str) or not 1 <= len(selector) <= 200
            or not isinstance(fields,dict) or len(fields)>10 or any(
                not isinstance(k,str) or not 1 <= len(k) <= 100 or not isinstance(v,str)
                or len(v)>1000 for k,v in fields.items()) or len(json.dumps(fields))>2000):
        raise NetworkError('invalid_arguments','Choose one short form selector and at most ten short field values.')


def proposal(observed, selector, fields, policy):
    validate_fields(selector,fields)
    if not isinstance(observed,dict):
        raise NetworkError('invalid_arguments', 'Choose one ordinary native form and at most ten short field values.')
    if observed.get('method') != 'POST' or observed.get('enctype') != 'application/x-www-form-urlencoded':
        raise NetworkError('action_denied', 'Only explicit native POST forms with URL-encoded fields are supported.')
    page = destination(observed['page'], policy).url
    target = destination(observed['action'], policy).url
    if urlsplit(page).scheme == 'https' and urlsplit(target).scheme != 'https':
        raise NetworkError('network_denied', 'A secure form cannot submit over HTTP.')
    controls = observed.get('controls')
    if not isinstance(controls,list) or len(controls)>30:
        raise NetworkError('action_denied','The form controls cannot be inspected safely.')
    values, names = [], set()
    for control in controls:
        name = control['name']
        if name in names or control['type'] in ('file','password','image'):
            raise NetworkError('action_denied','Duplicate, password and file controls are not supported.')
        names.add(name)
        if name in fields and control['type'] in ('hidden','submit','button','reset'):
            raise NetworkError('action_denied','Hidden fields and submit controls cannot be changed.')
        values.append((name, fields.get(name, control['value'])))
    if set(fields) - names:
        raise NetworkError('invalid_arguments','A requested field is not part of the observed form.')
    body = urlencode(values).encode('ascii')
    if len(body)>4096:
        raise NetworkError('resource_limit','The form body exceeds 4096 bytes.')
    canonical = json.dumps({'page':page,'selector':selector,'observed':observed,'fields':fields,
                            'destination':target,'body':body.decode('ascii')},sort_keys=True,ensure_ascii=True)
    return {'kind':'browser_proposal','page':page,'selector':selector,'fields':fields,
            'destination':target,'method':'POST','body':body.decode('ascii'),
            'fingerprint':hashlib.sha256(canonical.encode('ascii')).hexdigest()}
