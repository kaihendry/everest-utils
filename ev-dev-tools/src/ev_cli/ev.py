#!/usr/bin/env -S python3 -tt
# -*- coding: utf-8 -*-
#
# SPDX-License-Identifier: Apache-2.0
# Copyright 2020 - 2022 Pionix GmbH and Contributors to EVerest
#
"""
author: aw@pionix.de
FIXME (aw): Module documentation.
"""

from . import __version__
from . import helpers

from datetime import datetime
from pathlib import Path
import jinja2 as j2
import argparse


# Global variables
everest_dir = None
GENERATED_PREFIX = "build/generated"
GENERATED_INCLUDE_PREFIX = "include/generated"
GENERATED_SOURCE_PREFIX = "src"

# jinja template environment and global variable
env = j2.Environment(loader=j2.FileSystemLoader(Path(__file__).parent / 'templates'),
                     lstrip_blocks=True, trim_blocks=True, undefined=j2.StrictUndefined,
                     keep_trailing_newline=True)

templates = {
    'interface_req.hpp': env.get_template('interface_req.hpp.j2'),
    'interface_req.cpp': env.get_template('interface_req.cpp.j2'),
    'interface_impl.hpp': env.get_template('interface_impl.hpp.j2'),
    'interface_impl.cpp': env.get_template('interface_impl.cpp.j2'),
    'module.cpp': env.get_template('module.cpp.j2'),
    'module.hpp': env.get_template('module.hpp.j2'),
    'json_file.cpp': env.get_template('json_file.cpp.j2'),
    'ld-ev.cpp': env.get_template('ld-ev.cpp.j2'),
    'cmakelists': env.get_template('CMakeLists.txt.j2'),
    'mod_deps.cmake': env.get_template('mod_deps.cmake.j2')
}

validators = {}

# Function declarations


def setup_jinja_env():
    env.globals['timestamp'] = datetime.utcnow()
    # FIXME (aw): which repo to use? everest or everest-framework?
    # env.globals['git'] = helpers.gather_git_info(everest_dir)
    env.filters['snake_case'] = helpers.snake_case
    env.filters['create_dummy_result'] = helpers.create_dummy_result


def generate_tmpl_data_for_if(interface, if_def):
    vars = []
    for var, var_info in if_def.get('vars', {}).items():
        type_info = helpers.build_type_info(var, var_info['type'])

        vars.append(type_info)

    cmds = []
    for cmd, cmd_info in if_def.get('cmds', {}).items():
        args = []
        for arg, arg_info in cmd_info.get('arguments', {}).items():
            type_info = helpers.build_type_info(arg, arg_info['type'])

            args.append(type_info)

        result_type_info = None
        if 'result' in cmd_info:
            result_info = cmd_info['result']

            result_type_info = helpers.build_type_info(None, result_info['type'])

        cmds.append({'name': cmd, 'args': args, 'result': result_type_info})

    tmpl_data = {
        'info': {
            'base_class_header': f'generated/{interface}/Implementation.hpp',
            'interface': interface,
            'desc': if_def['description'],
        },
        'vars': vars,
        'cmds': cmds
    }

    return tmpl_data


def generate_tmpl_data_for_module(module, module_def):
    provides = []
    for impl, impl_info in module_def.get('provides', {}).items():
        config = []
        for conf_id, conf_info in impl_info.get('config', {}).items():
            type_info = helpers.build_type_info(conf_id, conf_info['type'])
            config.append(type_info)

        if_def, last_mtime = load_interface_defintion(impl_info['interface'])
        if_tmpl = generate_tmpl_data_for_if(impl_info['interface'], if_def)

        provides.append({
            'id': impl,
            'type': impl_info['interface'],
            'desc': impl_info['description'],
            'config': config,
            'tmpl': if_tmpl,
            'is_publishing': True if if_tmpl['vars'] else False,
            'is_callable': True if if_tmpl['cmds'] else False
        })

    requires = []
    for requirement_id, req_info in module_def.get('requires', {}).items():
        # min_connections=1 and max_connections=1 is the default if not provided otherwise (see manifest meta schema)
        is_vector = not (
            ('min_connections' not in req_info or req_info['min_connections'] == 1) and
            ('max_connections' not in req_info or req_info['max_connections'] == 1))
        requires.append({
            'id': requirement_id,
            'is_vector': is_vector,
            'type': req_info['interface'],
        })

    module_config = [helpers.build_type_info(conf_id, conf_info['type'])
                     for conf_id, conf_info in module_def.get('config', {}).items()]

    impl_configs = [impl for impl in provides if impl['config']]

    tmpl_data = {
        'info': {
            'name': module,
            'class_name': module,  # FIXME (aw): enforce capital case?
            'desc': module_def['description'],
            'enable_external_mqtt': module_def.get('enable_external_mqtt', False)
        },
        'provides': provides,
        'publishing_provides': [impl for impl in provides if impl['is_publishing']],
        'callable_provides': [impl for impl in provides if impl['is_callable']],
        'requires': requires,
        'configs': {
            'module': module_config,
            'implementations': impl_configs
        } if (module_config or impl_configs) else None,
    }

    return tmpl_data


def construct_impl_file_paths(impl):
    interface = impl['type']
    common_part = f'{impl["id"]}/{interface}'
    return (f'{common_part}Impl.hpp', f'{common_part}Impl.cpp')


def set_impl_specific_path_vars(tmpl_data, output_path):
    """Set cpp_file_rel_path and class_header vars to implementation template data."""
    for impl in tmpl_data['provides']:
        (impl['class_header'], impl['cpp_file_rel_path']) = construct_impl_file_paths(impl)


def generate_module_source_files(module_name, output_dir):
    loader_files = []

    mod_path = everest_dir / f'modules/{module_name}/manifest.json'
    mod_def = helpers.load_validated_module_def(mod_path, validators['module'])
    tmpl_data = generate_tmpl_data_for_module(module_name, mod_def)

    tmpl_data['manifest_json'] = {
        **helpers.generate_cpp_inline_representation(mod_path),
        'name': 'manifest_json',
        'namespace': 'module'
    }

    set_impl_specific_path_vars(tmpl_data, mod_path.parent)

    # module.hpp
    tmpl_data['info']['hpp_guard'] = f'GENERATED_MODULE_{helpers.snake_case(module_name).upper()}_HPP'
    loader_files.append({
        'filename': 'module.hpp',
        'path': output_dir / GENERATED_INCLUDE_PREFIX / 'module' / f'{module_name}.hpp',
        'printable_name': f'{module_name}.hpp',
        'content': templates['module.hpp'].render(tmpl_data),
        'last_mtime': mod_path.stat().st_mtime
    })

    # ld-ev.cpp
    loader_files.append({
        'filename': 'ld-ev.cpp',
        'path': output_dir / GENERATED_SOURCE_PREFIX / 'module' / module_name / 'ld-ev.cpp',
        'printable_name': f'{module_name}/ld-ev.cpp',
        'content': templates['ld-ev.cpp'].render(tmpl_data),
        'last_mtime': mod_path.stat().st_mtime
    })

    # FIXME (aw): needs to be refactored
    template_mtime = Path(templates['mod_deps.cmake'].filename).stat().st_mtime

    # mod_deps.cmake
    loader_files.append({
        'filename': 'mod_deps.cmake',
        'path': output_dir / GENERATED_SOURCE_PREFIX / 'module' / module_name / 'mod_deps.cmake',
        'printable_name': f'{module_name}/mod_deps.cmake',
        'content': templates['mod_deps.cmake'].render(tmpl_data),
        'last_mtime': max(mod_path.stat().st_mtime, template_mtime)
    })

    # manifest.cpp
    loader_files.append({
        'filename': 'manifest.h',
        'path': output_dir / GENERATED_SOURCE_PREFIX / 'module' / module_name / 'manifest.cpp',
        'printable_name': f'{module_name}/manifest.cpp',
        'content': templates['json_file.cpp'].render(tmpl_data['manifest_json']),
        'last_mtime': mod_path.stat().st_mtime
    })

    return loader_files


def create_module_files(mod, update_flag):
    mod_files = {'core': []}
    mod_path = everest_dir / f'modules/{mod}/manifest.json'
    mod_def = helpers.load_validated_module_def(mod_path, validators['module'])

    tmpl_data = generate_tmpl_data_for_module(mod, mod_def)
    output_path = mod_path.parent
    # FIXME (aw): we might move the following function into generate_tmp_data_for_module
    set_impl_specific_path_vars(tmpl_data, output_path)

    cmakelists_file = output_path / 'CMakeLists.txt'
    mod_files['core'].append({
        'abbr': 'cmakelists',
        'path': cmakelists_file,
        'content': templates['cmakelists'].render(tmpl_data),
        'last_mtime': mod_path.stat().st_mtime
    })

    module_cpp_file = output_path / 'module.cpp'
    mod_files['core'].append({
        'abbr': 'module.cpp',
        'path': module_cpp_file,
        'content': templates['module.cpp'].render(tmpl_data),
        'last_mtime': mod_path.stat().st_mtime
    })

    for file_info in mod_files['core']:
        file_info['printable_name'] = file_info['path'].relative_to(output_path)

    return mod_files


def load_interface_defintion(interface):
    if_path = everest_dir / f'interfaces/{interface}.json'

    if_def = helpers.load_validated_interface_def(if_path, validators['interface'])

    if 'vars' not in if_def:
        if_def['vars'] = {}
    if 'cmds' not in if_def:
        if_def['cmds'] = {}

    last_mtime = if_path.stat().st_mtime

    return if_def, last_mtime


def generate_interface_source_files(interface, all_interfaces_flag, output_dir):
    if_path = everest_dir / f'interfaces/{interface}.json'

    if_parts = {'req_hpp': None, 'req_cpp': None, 'impl_hpp': None, 'impl_cpp': None, 'json': None}

    try:
        if_def, last_mtime = load_interface_defintion(interface)
    except Exception as e:
        if not all_interfaces_flag:
            raise
        else:
            print(f'Ignoring interface {interface} with reason: {e}')
            return

    tmpl_data = generate_tmpl_data_for_if(interface, if_def)
    tmpl_data['interface_json'] = {
        **helpers.generate_cpp_inline_representation(if_path),
        'name': 'interface_json',
        'namespace': f'everest::interface::{interface}'
    }

    # requirement / export definitions
    tmpl_data['info']['hpp_guard'] = f'GENERATED_INTERFACE_{helpers.snake_case(interface).upper()}_REQ_HPP'

    hpp_file = output_dir / GENERATED_INCLUDE_PREFIX / 'interface' / f'{interface}_req.hpp'
    if_parts['req_hpp'] = {
        'path': hpp_file,
        'content': templates['interface_req.hpp'].render(tmpl_data),
        'last_mtime': last_mtime,
        'printable_name': hpp_file.relative_to(output_dir)
    }

    cpp_file = output_dir / GENERATED_SOURCE_PREFIX / 'interface' / f'{interface}_req.cpp'
    if_parts['req_cpp'] = {
        'path': cpp_file,
        'content': templates['interface_req.cpp'].render(tmpl_data),
        'last_mtime': last_mtime,
        'printable_name': cpp_file.relative_to(output_dir)
    }

    # implementation definitions
    tmpl_data['info']['hpp_guard'] = f'GENERATED_INTERFACE_{helpers.snake_case(interface).upper()}_IMPL_HPP'

    impl_hpp_file = output_dir / GENERATED_INCLUDE_PREFIX / 'interface' / f'{interface}_impl.hpp'
    if_parts['impl_hpp'] = {
        'path': impl_hpp_file,
        'content': templates['interface_impl.hpp'].render(tmpl_data),
        'last_mtime': last_mtime,
        'printable_name': impl_hpp_file.relative_to(output_dir)
    }

    impl_cpp_file = output_dir / GENERATED_SOURCE_PREFIX / 'interface' / f'{interface}_impl.cpp'
    if_parts['impl_cpp'] = {
        'path': impl_cpp_file,
        'content': templates['interface_impl.cpp'].render(tmpl_data),
        'last_mtime': last_mtime,
        'printable_name': impl_cpp_file.relative_to(output_dir)
    }

    interface_json_file = output_dir / GENERATED_SOURCE_PREFIX / 'interface' / f'{interface}_json.cpp'
    if_parts['json'] = {
        'path': interface_json_file,
        'content': templates['json_file.cpp'].render(tmpl_data['interface_json']),
        'last_mtime': last_mtime,
        'printable_name': interface_json_file.relative_to(output_dir)
    }

    return if_parts


def module_create(args):
    create_strategy = 'force-create' if args.force else 'create'

    mod_files = create_module_files(args.module, False)

    if args.only == 'which':
        helpers.print_available_mod_files(mod_files)
        return
    else:
        try:
            helpers.filter_mod_files(args.only, mod_files)
        except Exception as err:
            print(err)
            return

    for file_info in mod_files['core']:
        if not args.disable_clang_format:
            helpers.clang_format(args.clang_format_file, file_info)

        helpers.write_content_to_file(file_info, create_strategy, args.diff)


def module_update(args):
    primary_update_strategy = 'force-update' if args.force else 'update'
    update_strategy = {'module.cpp': 'update-if-non-existent'}
    for file_name in ['cmakelists', 'module.hpp']:
        update_strategy[file_name] = primary_update_strategy

    # FIXME (aw): refactor out this only handling and rename it properly
    mod_files = create_module_files(args.module, True)

    if args.only == 'which':
        helpers.print_available_mod_files(mod_files)
        return
    else:
        try:
            helpers.filter_mod_files(args.only, mod_files)
        except Exception as err:
            print(err)
            return

    if not args.disable_clang_format:
        for file_info in mod_files['core'] + mod_files['interfaces']:
            helpers.clang_format(args.clang_format_file, file_info)

    for file_info in mod_files['core']:
        helpers.write_content_to_file(file_info, update_strategy[file_info['abbr']], args.diff)

    for file_info in mod_files['interfaces']:
        if file_info['abbr'].endswith('.hpp'):
            helpers.write_content_to_file(file_info, primary_update_strategy, args.diff)
        else:
            helpers.write_content_to_file(file_info, 'update-if-non-existent', args.diff)


def module_generate_sources(args):
    output_dir = Path(args.output_dir).resolve() if args.output_dir else everest_dir / GENERATED_PREFIX

    primary_update_strategy = 'force-update' if args.force else 'update'

    loader_files = generate_module_source_files(args.module, output_dir)

    if not args.disable_clang_format:
        for file_info in loader_files:
            helpers.clang_format(args.clang_format_file, file_info)

    for file_info in loader_files:
        helpers.write_content_to_file(file_info, primary_update_strategy)


def interface_generate_sources(args):
    output_dir = Path(args.output_dir).resolve() if args.output_dir else everest_dir / GENERATED_PREFIX

    primary_update_strategy = 'force-update' if args.force else 'update'

    interfaces = args.interfaces
    all_interfaces = False
    if not interfaces:
        all_interfaces = True
        if_dir = everest_dir / 'interfaces'
        interfaces = [if_path.stem for if_path in if_dir.iterdir() if (if_path.is_file() and if_path.suffix == '.json')]

    for interface in interfaces:
        if_parts = generate_interface_source_files(interface, all_interfaces, output_dir)

        if not args.disable_clang_format:
            for part in if_parts.values():
                helpers.clang_format(args.clang_format_file, part)

        for part in if_parts.values():
            helpers.write_content_to_file(part, primary_update_strategy, args.diff)


def helpers_genuuids(args):
    if (args.count <= 0):
        raise Exception(f'Invalid number ("{args.count}") of uuids to generate')
    helpers.generate_some_uuids(args.count)


def main():
    global validators, everest_dir

    parser = argparse.ArgumentParser(description='Everest command line tool')
    parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    parser.set_defaults(needs_everest=True)

    common_parser = argparse.ArgumentParser(add_help=False)
    # parser.add_argument("--framework-dir", "-fd", help='directory of everest framework')
    common_parser.add_argument("--everest-dir", "-ed", type=str,
                               help='everest directory containing the interface definitions (default: .)', default=str(Path.cwd()))
    common_parser.add_argument("--framework-dir", "-fd", type=str,
                               help='everest framework directory containing the schema definitions (default: ../everest-framework)', default=str(Path.cwd() / '../everest-framework'))
    common_parser.add_argument("--clang-format-file", type=str, default=str(Path.cwd()),
                               help='Path to the directory, containing the .clang-format file (default: .)')
    common_parser.add_argument("--disable-clang-format", action='store_true', default=False,
                               help="Set this flag to disable clang-format")

    subparsers = parser.add_subparsers(metavar='<command>', help='available commands', required=True)
    parser_mod = subparsers.add_parser('module', aliases=['mod'], help='module related actions')
    parser_if = subparsers.add_parser('interface', aliases=['if'], help='interface related actions')
    parser_hlp = subparsers.add_parser('helpers', aliases=['hlp'], help='helper actions')

    mod_actions = parser_mod.add_subparsers(metavar='<action>', help='available actions', required=True)
    mod_create_parser = mod_actions.add_parser('create', aliases=['c'], parents=[
                                               common_parser], help='create module(s)')
    mod_create_parser.add_argument('module', type=str, help='name of the module, that should be created')
    mod_create_parser.add_argument('-f', '--force', action='store_true', help='force overwriting - use with care!')
    mod_create_parser.add_argument('-d', '--diff', '--dry-run', action='store_true',
                                   help='show resulting diff on create or overwrite')
    mod_create_parser.add_argument('--only', type=str,
                                   help='Comma separated filter list of module files, that should be created.  '
                                   'For a list of available files use "--only which".')
    mod_create_parser.set_defaults(action_handler=module_create)

    mod_update_parser = mod_actions.add_parser('update', aliases=['u'], parents=[
                                               common_parser], help='update module(s)')
    mod_update_parser.add_argument('module', type=str, help='name of the module, that should be updated')
    mod_update_parser.add_argument('-f', '--force', action='store_true', help='force overwriting')
    mod_update_parser.add_argument('-d', '--diff', '--dry-run', action='store_true', help='show resulting diff')
    mod_update_parser.add_argument('--only', type=str,
                                   help='Comma separated filter list of module files, that should be updated.  '
                                   'For a list of available files use "--only which".')
    mod_update_parser.set_defaults(action_handler=module_update)

    # FIXME (aw): rename genld and genhdr
    mod_genld_parser = mod_actions.add_parser(
        'generate-sources', aliases=['gs'], parents=[common_parser], help='generate everest loader')
    mod_genld_parser.add_argument(
        'module', type=str, help='name of the module, for which the loader should be generated')
    mod_genld_parser.add_argument('-f', '--force', action='store_true', help='force overwriting')
    mod_genld_parser.add_argument('-o', '--output-dir', type=str,
                                  help=f'Output directory for generated files (default: {{everest-dir}}/{GENERATED_PREFIX})')
    mod_genld_parser.set_defaults(action_handler=module_generate_sources)

    if_actions = parser_if.add_subparsers(metavar='<action>', help='available actions', required=True)
    if_genhdr_parser = if_actions.add_parser(
        'generate-sources', aliases=['gs'], parents=[common_parser], help='generate sources')
    if_genhdr_parser.add_argument('-f', '--force', action='store_true', help='force overwriting')
    if_genhdr_parser.add_argument('-o', '--output-dir', type=str, help='Output directory for generated files '
                                  f'headers (default: {{everest-dir}}/{GENERATED_PREFIX})')
    if_genhdr_parser.add_argument('-d', '--diff', '--dry-run', action='store_true', help='show resulting diff')
    if_genhdr_parser.add_argument('interfaces', nargs='*', help='a list of interfaces, for which header files should '
                                  'be generated - if no interface is given, all will be processed and non-processable '
                                  'will be skipped')
    if_genhdr_parser.set_defaults(action_handler=interface_generate_sources)

    hlp_actions = parser_hlp.add_subparsers(metavar='<action>', help='available actions', required=True)
    hlp_genuuid_parser = hlp_actions.add_parser('generate-uuids', help='generete uuids')
    hlp_genuuid_parser.add_argument('count', type=int, default=3)
    hlp_genuuid_parser.set_defaults(action_handler=helpers_genuuids, needs_everest=False)

    args = parser.parse_args()

    if args.needs_everest:
        everest_dir = Path(args.everest_dir).resolve()
        if not (everest_dir / 'interfaces').exists():
            print('The default (".") xor supplied (via --everest-dir) everest directory\n'
                  'doesn\'t contain an "interface" directory and therefore does not seem to be valid.\n'
                  f'dir: {everest_dir}')
            exit(1)

        setup_jinja_env()

        framework_dir = Path(args.framework_dir).resolve()
        if not (framework_dir / 'schemas').exists():
            print('The default ("../everest-framework") xor supplied (via --framework-dir) everest framework directory\n'
                  'doesn\'t contain an "schemas" directory and therefore does not seem to be valid.\n'
                  f'dir: {framework_dir}')
            exit(1)

        validators = helpers.load_validators(framework_dir / 'schemas')

    args.action_handler(args)


if __name__ == '__main__':
    main()
