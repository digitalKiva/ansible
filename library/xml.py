#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright 2014, Red Hat, Inc.
# Tim Bielawa <tbielawa@redhat.com>
# Magnus Hedemark <mhedemar@redhat.com>
#
# This file is part of Ansible
#
# Ansible is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Ansible is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Ansible.  If not, see <http://www.gnu.org/licenses/>.


ANSIBLE_METADATA = {'metadata_version': '1.0',
                    'status': ['preview'],
                    'supported_by': 'community'}

DOCUMENTATION = r'''
---
module: xml
short_description: Manage bits and pieces of XML files or strings
description:
- A CRUD-like interface to managing bits of XML files.
- You might also be interested in a brief tutorial from
  U(http://www.w3schools.com/xpath/).
version_added: '2.4'
options:
  path:
    description:
    - Path to the file to operate on. File must exist ahead of time.
    - This parameter is required, unless C(xmlstring) is given.
    required: yes
    aliases: [ dest, file ]
  xmlstring:
    description:
    - A string containing XML on which to operate.
    - This parameter is required, unless C(path) is given.
    required: yes
  xpath:
    description:
    - A valid XPath expression describing the item(s) you want to manipulate.
    - Operates on the document root, C(/), by default.
    default: /
  namespaces:
    description:
    - The namespace C(prefix:uri) mapping for the XPath expression.
    - Needs to be a C(dict), not a C(list) of items.
  state:
    description:
      - Set or remove an xpath selection (node(s), attribute(s)).
    default: present
    choices: [ absent, present ]
    aliases: [ ensure ]
  value:
    description:
      - Desired state of the selected attribute.
      - Either a string, or to unset a value, the Python C(None) keyword (YAML Equivalent, C(null)).
      - Elements default to no value (but present).
      - Attributes default to an empty string.
  add_children:
    description:
    - Add additional child-element(s) to a selected element.
    - Child elements must be given in a list and each item may be either a string
      (eg. C(children=ansible) to add an empty C(<ansible/>) child element),
      or a hash where the key is an element name and the value is the element value.
  set_children:
    description:
    - Set the the child-element(s) of a selected element.
    - Removes any existing children.
    - Child elements must be specified as in C(add_children).
  count:
    description:
    - Search for a given C(xpath) and provide the count of any matches.
    type: 'bool'
    default: 'no'
  print_match:
    description:
    - Search for a given C(xpath) and print out any matches.
    type: 'bool'
    default: 'no'
  pretty_print:
    description:
    - Pretty print XML output.
    type: 'bool'
    default: 'no'
  content:
    description:
    - Search for a given C(xpath) and get content.
    choices: [ attribute, text ]
  input_type:
    description:
      - Type of input for C(add_children) and C(set_children).
    choices: [ xml, yaml ]
    default: yaml
requirements:
- lxml >= 2.3.0
notes:
- This module does not handle complicated xpath expressions.
  So limit xpath selectors to simple expressions.
- Beware that in case your XML elements are namespaced,
  you need to use the C(namespaces) parameter.
author:
- Tim Bielawa (@tbielawa)
- Magnus Hedemark (@magnus919)
'''

EXAMPLES = r'''
- name: Remove the subjective attribute of the rating element
  xml:
    path: /foo/bar.xml
    xpath: /business/rating/@subjective
    state: absent

- name: Set the rating to 11
  xml:
    path: /foo/bar.xml
    xpath: /business/rating
    value: 11

# Retrieve and display the number of nodes
- name: Get count of beers nodes
  xml:
    path: /foo/bar.xml
    xpath: /business/beers/beer
    count: yes
  register: hits

- debug:
    var: hits.count

- name: Add a phonenumber element to the business element
  xml:
    path: /foo/bar.xml
    xpath: /business/phonenumber
    value: 555-555-1234

- name: Add several more beers to the beers element
  xml:
    path: /foo/bar.xml
    xpath: /business/beers
    add_children:
    - beer: Old Rasputin
    - beer: Old Motor Oil
    - beer: Old Curmudgeon

- name: Add a validxhtml element to the website element
  xml:
    path: /foo/bar.xml
    xpath: /business/website/validxhtml

- name: Add an empty validatedon attribute to the validxhtml element
  xml:
    path: /foo/bar.xml
    xpath: /business/website/validxhtml/@validatedon

- name: Remove all children from the website element (option 1)
  xml:
    path: /foo/bar.xml
    xpath: /business/website/*
    state: absent

- name: Remove all children from the website element (option 2)
  xml:
    path: /foo/bar.xml
    xpath: /business/website
    children: []
'''

RETURN = r'''# '''

import os
import re
import traceback

from io import BytesIO

HAS_LXML = True
try:
    from lxml import etree
    import lxml
except ImportError:
    HAS_LXML = False

try:
    import json
except:
    import simplejson as json

from ansible.module_utils.basic import AnsibleModule, get_exception
from ansible.module_utils.six import iteritems


def print_match(module, tree, xpath, namespaces):
    match = tree.xpath(xpath, namespaces=namespaces)
    match_xpaths = []
    for m in match:
        match_xpaths.append(tree.getpath(m))
    match_str = json.dumps(match_xpaths)
    msg = "selector '%s' match: %s" % (xpath, match_str)
    finish(module, tree, xpath, namespaces, changed=False, msg=msg)


def count_nodes(module, tree, xpath, namespaces):
    """ Return the count of nodes matching the xpath """
    hits = tree.xpath("count(/%s)" % xpath, namespaces=namespaces)
    finish(module, tree, xpath, namespaces, changed=False, msg=int(hits), hitcount=int(hits))


def is_node(tree, xpath, namespaces):
    """ Test if a given xpath matches anything and if that match is a node.

    For now we just assume you're only searching for one specific thing."""
    if xpath_matches(tree, xpath, namespaces):
        # OK, it found something
        match = tree.xpath(xpath, namespaces=namespaces)
        if isinstance(match[0], lxml.etree._Element):
            return True

    return False


def is_attribute(tree, xpath, namespaces):
    """ Test if a given xpath matches and that match is an attribute

    An xpath attribute search will only match one item"""
    if xpath_matches(tree, xpath, namespaces):
        match = tree.xpath(xpath, namespaces=namespaces)
        if isinstance(match[0], lxml.etree._ElementStringResult):
            return True
    return False


def xpath_matches(tree, xpath, namespaces):
    """ Test if a node exists """
    if tree.xpath(xpath, namespaces=namespaces):
        return True
    else:
        return False


def delete_xpath_target(module, tree, xpath, namespaces):
    """ Delete an attribute or element from a tree """
    try:
        for result in tree.xpath(xpath, namespaces=namespaces):
            if not module.check_mode:
                # Get the xpath for this result
                if is_attribute(tree, xpath, namespaces):
                    # Delete an attribute
                    parent = result.getparent()
                    # Pop this attribute match out of the parent
                    # node's 'attrib' dict by using this match's
                    # 'attrname' attribute for the key
                    parent.attrib.pop(result.attrname)
                elif is_node(tree, xpath, namespaces):
                    # Delete an element
                    result.getparent().remove(result)
    except Exception:
        e = get_exception()
        module.fail_json(msg="Couldn't delete xpath target: %s (%s)" % (xpath, e))
    else:
        finish(module, tree, xpath, namespaces, changed=True)


def replace_children_of(children, match):
    for element in match.getchildren():
        match.remove(element)
    match.extend(children)


def set_target_children_inner(module, tree, xpath, namespaces, children, in_type):
    matches = tree.xpath(xpath, namespaces=namespaces)

    # Create a list of our new children
    children = children_to_nodes(module, children, in_type)
    children_as_string = [lxml.etree.tostring(c) for c in children]

    changed = False

    # xpaths always return matches as a list, so....
    for match in matches:
        # Check if elements differ
        if len(match.getchildren()) == len(children):
            for idx, element in enumerate(match.getchildren()):
                if lxml.etree.tostring(element) != children_as_string[idx]:
                    if not module.check_mode:
                        replace_children_of(children, match)
                    changed = True
                    break
        else:
            if not module.check_mode:
                replace_children_of(children, match)
            changed = True

    return changed


def set_target_children(module, tree, xpath, namespaces, children, in_type):
    changed = set_target_children_inner(module, tree, xpath, namespaces, children, in_type)
    # Write it out
    finish(module, tree, xpath, namespaces, changed=changed)


def add_target_children(module, tree, xpath, namespaces, children, in_type):
    if is_node(tree, xpath, namespaces):
        new_kids = children_to_nodes(module, children, in_type)
        for node in tree.xpath(xpath, namespaces=namespaces):
            if not module.check_mode:
                node.extend(new_kids)
        finish(module, tree, xpath, namespaces, changed=True)
    else:
        finish(module, tree, xpath, namespaces)

_ident = "[a-zA-Z-][a-zA-Z0-9_\-\.]*"
_nsIdent = _ident + "|" + _ident + ":" + _ident
# Note: we can't reasonably support the 'if you need to put both ' and " in a string, concatenate
# strings wrapped by the other delimiter' XPath trick, especially as simple XPath.
_xpstr = "('(?:.*)'|\"(?:.*)\")"

_re_splitSimpleLast = re.compile("^(.*)/(" + _nsIdent + ")$")
_re_splitSimpleLastEqValue = re.compile("^(.*)/(" + _nsIdent + ")/text\\(\\)=" + _xpstr + "$")
_re_splitSimpleAttrLast = re.compile("^(.*)/(@(?:" + _nsIdent + "))$")
_re_splitSimpleAttrLastEqValue = re.compile("^(.*)/(@(?:" + _nsIdent + "))=" + _xpstr + "$")

_re_splitSubLast = re.compile("^(.*)/(" + _nsIdent + ")\\[(.*)\\]$")

_re_splitOnlyEqValue = re.compile("^(.*)/text\\(\\)=" + _xpstr + "$")


def _extract_xpstr(g):
    return g[1:-1]


def split_xpath_last(xpath):
    """split an XPath of the form /foo/bar/baz into /foo/bar and baz"""
    xpath = xpath.strip()
    m = _re_splitSimpleLast.match(xpath)
    if m:
        # requesting an element to exist
        return (m.group(1), [(m.group(2), None)])
    m = _re_splitSimpleLastEqValue.match(xpath)
    if m:
        # requesting an element to exist with an inner text
        return (m.group(1), [(m.group(2), _extract_xpstr(m.group(3)))])

    m = _re_splitSimpleAttrLast.match(xpath)
    if m:
        # requesting an attribute to exist
        return (m.group(1), [(m.group(2), None)])
    m = _re_splitSimpleAttrLastEqValue.match(xpath)
    if m:
        # requesting an attribute to exist with a value
        return (m.group(1), [(m.group(2), _extract_xpstr(m.group(3)))])

    m = _re_splitSubLast.match(xpath)
    if m:
        content = map(lambda x: x.strip(), m.group(3).split(" and "))

        return (m.group(1), [('/' + m.group(2), content)])

    m = _re_splitOnlyEqValue.match(xpath)
    if m:
        # requesting a change of inner text
        return (m.group(1), [("", _extract_xpstr(m.group(2)))])
    return (xpath, [])


def nsnameToClark(name, namespaces):
    if ":" in name:
        (nsname, rawname) = name.split(":")
        # return "{{%s}}%s" % (namespaces[nsname], rawname)
        return "{{{0}}}{1}".format(namespaces[nsname], rawname)
    else:
        # no namespace name here
        return name


def check_or_make_target(module, tree, xpath, namespaces):
    (inner_xpath, changes) = split_xpath_last(xpath)
    if (inner_xpath == xpath) or (changes is None):
        module.fail_json(msg="Can't process Xpath %s in order to spawn nodes! tree is %s" %
                             (xpath, etree.tostring(tree, pretty_print=True)))
        return False

    changed = False

    if not is_node(tree, inner_xpath, namespaces):
        changed = check_or_make_target(module, tree, inner_xpath, namespaces)

    # we test again after calling check_or_make_target
    if is_node(tree, inner_xpath, namespaces) and changes:
        for (eoa, eoa_value) in changes:
            if eoa and eoa[0] != '@' and eoa[0] != '/':
                # implicitly creating an element
                new_kids = children_to_nodes(module, [nsnameToClark(eoa, namespaces)], "yaml")
                if eoa_value:
                    for nk in new_kids:
                        nk.text = eoa_value

                for node in tree.xpath(inner_xpath, namespaces=namespaces):
                    if not module.check_mode:
                        node.extend(new_kids)
                    changed = True
                # module.fail_json(msg="now tree=%s" % etree.tostring(tree, pretty_print=True))
            elif eoa and eoa[0] == '/':
                element = eoa[1:]
                new_kids = children_to_nodes(module, [nsnameToClark(element, namespaces)], "yaml")
                for node in tree.xpath(inner_xpath, namespaces=namespaces):
                    if not module.check_mode:
                        node.extend(new_kids)
                    for nk in new_kids:
                        for subexpr in eoa_value:
                            # module.fail_json(msg="element=%s subexpr=%s node=%s now tree=%s" %
                            #                      (element, subexpr, etree.tostring(node, pretty_print=True), etree.tostring(tree, pretty_print=True))
                            check_or_make_target(module, nk, "./" + subexpr, namespaces)
                    changed = True

                # module.fail_json(msg="now tree=%s" % etree.tostring(tree, pretty_print=True))
            elif eoa == "":
                for node in tree.xpath(inner_xpath, namespaces=namespaces):
                    if (node.text != eoa_value):
                        node.text = eoa_value
                        changed = True

            elif eoa and eoa[0] == '@':
                attribute = nsnameToClark(eoa[1:], namespaces)

                for element in tree.xpath(inner_xpath, namespaces=namespaces):
                    changing = (attribute not in element.attrib or element.attrib[attribute] != eoa_value)

                    if not module.check_mode and changing:
                        changed = changed or changing
                        if eoa_value is None:
                            value = ""
                        else:
                            value = eoa_value
                        element.attrib[attribute] = value

                    # module.fail_json(msg="arf %s changing=%s as curval=%s changed tree=%s" %
                    #       (xpath, changing, etree.tostring(tree, changing, element[attribute], pretty_print=True)))

            else:
                module.fail_json(msg="unknown tree transformation=%s" % etree.tostring(tree, pretty_print=True))

    return changed


def ensure_xpath_exists(module, tree, xpath, namespaces):
    changed = False

    if not is_node(tree, xpath, namespaces):
        changed = check_or_make_target(module, tree, xpath, namespaces)

    finish(module, tree, xpath, namespaces, changed)


def set_target_inner(module, tree, xpath, namespaces, attribute, value):
    changed = False

    try:
        if not is_node(tree, xpath, namespaces):
            changed = check_or_make_target(module, tree, xpath, namespaces)
    except Exception:
        e = get_exception()
        module.fail_json(msg="Xpath %s causes a failure: %s\n%s\n  -- tree is %s" %
                             (xpath, e, traceback.format_exc(e), etree.tostring(tree, pretty_print=True)))

    if not is_node(tree, xpath, namespaces):
        module.fail_json(msg="Xpath %s does not reference a node! tree is %s" %
                             (xpath, etree.tostring(tree, pretty_print=True)))

    for element in tree.xpath(xpath, namespaces=namespaces):
        if not attribute:
            changed = changed or (element.text != value)
            if not module.check_mode and (element.text != value):
                element.text = value
        else:
            changed = changed or (element.get(attribute) != value)
            if ":" in attribute:
                attr_ns, attr_name = attribute.split(":")
                # attribute = "{{%s}}%s" % (namespaces[attr_ns], attr_name)
                attribute = "{{{0}}}{1}".format(namespaces[attr_ns], attr_name)
            if not module.check_mode and (element.get(attribute) != value):
                element.set(attribute, value)

    return changed


def set_target(module, tree, xpath, namespaces, attribute, value):
    changed = set_target_inner(module, tree, xpath, namespaces, attribute, value)
    finish(module, tree, xpath, namespaces, changed)


def pretty(module, tree):
    xml_string = etree.tostring(tree, xml_declaration=True, encoding='UTF-8', pretty_print=module.params['pretty_print'])
    changed = False

    if module.params['path']:
        xml_file = module.params['path']

        xml_content = open(xml_file)
        try:
            if xml_string != xml_content.read():
                changed = True
                tree.write(xml_file, xml_declaration=True, encoding='UTF-8', pretty_print=module.params['pretty_print'])
        finally:
            xml_content.close()

        module.exit_json(changed=changed)

    elif module.params['xmlstring']:
        if xml_string != module.params['xmlstring']:
            changed = True

        module.exit_json(changed=changed, xmlstring=xml_string)


def get_element_text(module, tree, xpath, namespaces):
    if not is_node(tree, xpath, namespaces):
        module.fail_json(msg="Xpath %s does not reference a node!" % xpath)

    elements = []
    for element in tree.xpath(xpath, namespaces=namespaces):
        elements.append({element.tag: element.text})

    finish(module, tree, xpath, namespaces, changed=False, msg=len(elements), hitcount=len(elements), matches=elements)


def get_element_attr(module, tree, xpath, namespaces):
    if not is_node(tree, xpath, namespaces):
        module.fail_json(msg="Xpath %s does not reference a node!" % xpath)

    elements = []
    for element in tree.xpath(xpath, namespaces=namespaces):
        child = {}
        for key in element.keys():
            value = element.get(key)
            child.update({key: value})
        elements.append({element.tag: child})

    finish(module, tree, xpath, namespaces, changed=False, msg=len(elements), hitcount=len(elements), matches=elements)


def child_to_element(module, child, in_type):
    if in_type == 'xml':
        infile = BytesIO(child.encode('utf-8'))

        try:
            parser = etree.XMLParser()
            node = etree.parse(infile, parser)
            return node.getroot()
        except etree.XMLSyntaxError:
            e = get_exception()
            module.fail_json(msg="Error while parsing child element: %s" % e)
    elif in_type == 'yaml':
        if isinstance(child, str) or isinstance(child, unicode):
            return etree.Element(child)
        elif isinstance(child, dict):
            if len(child) > 1:
                module.fail_json(msg="Can only create children from hashes with one key")

            (key, value) = child.items()[0]
            if isinstance(value, dict):
                children = value.pop('_', None)

                node = etree.Element(key, value)

                if children is not None:
                    if not isinstance(children, list):
                        module.fail_json(msg="Invalid children type: %s, must be list." % type(children))

                    subnodes = children_to_nodes(module, children)
                    node.extend(subnodes)
            else:
                node = etree.Element(key)
                node.text = value
            return node
        else:
            module.fail_json(msg="Invalid child type: %s. Children must be either strings or hashes." % type(child))
    else:
        module.fail_json(msg="Invalid child input type: %s. Type must be either xml or yaml." % in_type)


def children_to_nodes(module=None, children=[], type='yaml'):
    """turn a str/hash/list of str&hash into a list of elements"""
    return [child_to_element(module, child, type) for child in children]


def finish(module, tree, xpath, namespaces, changed=False, msg="", hitcount=0, matches=[]):
    actions = dict(xpath=xpath, namespaces=namespaces, state=module.params['state'])

    if not changed:
        module.exit_json(changed=changed, actions=actions, msg=msg, count=hitcount, matches=matches)

    if module.params['path']:
        tree.write(module.params['path'], xml_declaration=True, encoding='UTF-8', pretty_print=module.params['pretty_print'])
        module.exit_json(changed=changed, actions=actions, msg=msg, count=hitcount, matches=matches)

    if module.params['xmlstring']:
        xml_string = etree.tostring(tree, xml_declaration=True, encoding='UTF-8', pretty_print=module.params['pretty_print'])
        module.exit_json(changed=changed, actions=actions, msg=msg, count=hitcount, matches=matches, xmlstring=xml_string)


def decode(value):
    # Convert value to unicode to use with lxml
    if not value or isinstance(value, unicode):
        return value
    elif isinstance(value, str):
        return value.decode('utf-8')
    elif isinstance(value, list):
        return [decode(v) for v in value]
    elif isinstance(value, dict):
        return dict((key, decode(val)) for key, val in iteritems(value))
    else:
        raise AttributeError('Undecodable value: type=%s, value=%s' % (type(value), value))


def main():
    module = AnsibleModule(
        argument_spec=dict(
            path=dict(type='path', aliases=['dest', 'file']),
            xmlstring=dict(type='str'),
            xpath=dict(type='str', default='/'),
            namespaces=dict(type='dict', default={}),
            state=dict(type='str', default='present', choices=['absent', 'present'], aliases=['ensure']),
            value=dict(),
            attribute=dict(),
            add_children=dict(type='list'),
            set_children=dict(type='list'),
            count=dict(type='bool', default=False),
            print_match=dict(type='bool', default=False),
            pretty_print=dict(type='bool', default=False),
            content=dict(type='str', choices=['attribute', 'text']),
            input_type=dict(type='str', default='yaml', choices=['xml', 'yaml'])
        ),
        supports_check_mode=True,
        mutually_exclusive=[
            ['value', 'set_children'],
            ['value', 'add_children'],
            ['set_children', 'add_children'],
            ['path', 'xmlstring'],
            ['content', 'set_children'],
            ['content', 'add_children'],
            ['content', 'value'],
        ]
    )

    xml_file = module.params['path']
    xml_string = module.params['xmlstring']
    xpath = module.params['xpath']
    namespaces = module.params['namespaces']
    state = module.params['state']
    value = decode(module.params['value'])
    attribute = module.params['attribute']
    set_children = decode(module.params['set_children'])
    add_children = decode(module.params['add_children'])
    pretty_print = module.params['pretty_print']
    content = module.params['content']
    input_type = module.params['input_type']
    print_match = module.params['print_match']
    count = module.params['count']

    if not HAS_LXML:
        module.fail_json(msg='The xml ansible module needs to have the lxml python library installed on the managed machine')

    # Check if the file exists
    if xml_string:
        infile = BytesIO(xml_string.encode('utf-8'))
    elif os.path.isfile(xml_file):
        infile = file(xml_file, 'r')
    else:
        module.fail_json(msg="The target XML source does not exist: %s" % xml_file)

    # Try to parse in the target XML file
    try:
        parser = etree.XMLParser(remove_blank_text=pretty_print)
        x = etree.parse(infile, parser)
    except etree.XMLSyntaxError:
        e = get_exception()
        module.fail_json(msg="Error while parsing path: %s" % e)

    if print_match:
        print_match(module, x, xpath, namespaces)

    if count:
        count_nodes(module, x, xpath, namespaces)

    if content == 'attribute':
        get_element_attr(module, x, xpath, namespaces)
    elif content == 'text':
        get_element_text(module, x, xpath, namespaces)

    # module.fail_json(msg="OK. Well, etree parsed the xml file...")

    # module.exit_json(what_did={"foo": "bar"}, changed=True)

    # File exists:
    if state == 'absent':
        # - absent: delete xpath target
        delete_xpath_target(module, x, xpath, namespaces)
        # Exit
    # - present: carry on

    # children && value both set?: should have already aborted by now
    # add_children && set_children both set?: should have already aborted by now

    # set_children set?
    if set_children:
        set_target_children(module, x, xpath, namespaces, set_children, input_type)

    # add_children set?
    if add_children:
        add_target_children(module, x, xpath, namespaces, add_children, input_type)

    # No?: Carry on

    # Is the xpath target an attribute selector?
    if value is not None:
        set_target(module, x, xpath, namespaces, attribute, value)

    # Format the xml only?
    if pretty_print:
        pretty(module, x)

    ensure_xpath_exists(module, x, xpath, namespaces)
    # module.fail_json(msg="don't know what to do")


if __name__ == '__main__':
    main()
