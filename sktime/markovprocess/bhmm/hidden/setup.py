
def configuration(parent_package='', top_path=None):
    from numpy.distutils.misc_util import Configuration

    config = Configuration('hidden', parent_package, top_path)
    config.add_extension('hidden', sources=['hidden.pyx', 'src/_hidden.c'],
                         include_dirs=['src'],
                         extra_compile_args=['-O3'])
    print(config)
    return config
